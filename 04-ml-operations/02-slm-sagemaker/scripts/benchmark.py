#!/usr/bin/env python3
"""Benchmark real do endpoint: aquecimento + sequencial (+ concorrente opcional).

Uso:
  python scripts/benchmark.py --release v1
  python scripts/benchmark.py --release v1 --sequential 20 --concurrent 4

Protocolo (spec §14, blueprint §14):
  1. 5 requisições de aquecimento (descartadas da métrica — cobrem o custo de
     primeira invocação/carregamento de contexto/prompt cache do llama.cpp);
  2. N requisições sequenciais medidas (10-20, default 15);
  3. opcionalmente, um lote concorrente (2-4 threads) só para registrar como
     o p95 se comporta sob paralelismo — não substitui a medida sequencial,
     que é a usada para congelar o envelope de latência.

O envelope de latência (`latency_envelope_ms`) é congelado a partir do p95
MEDIDO aqui, nunca de um valor da spec/documentação — ver `_freeze_envelope`
para a margem aplicada e o porquê.

Escreve/mescla `artifacts/evidence/benchmark.json` (chave por release — v1 e
v2 compartilham o mesmo arquivo, nunca um sobrescreve a chave do outro) e
`.generated/runtime.json["latency_envelope_ms"]`.

Convenção do lab: stdout = resultado (JSON), stderr = progresso.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab42 import evaluation, evidence, hf_release, inference
from lab42.aws import LabError, emit, log

WARMUP_REQUESTS = 5

# Margem sobre o p95 medido antes de congelar o envelope. 30% cobre a
# variância normal de CPU compartilhada do Academy (ml.m5.xlarge sem
# dedicated host) sem esconder uma regressão real de 2x — se o modelo
# começar a responder consistentemente 2x mais lento, o envelope ainda
# reprova. Documentado aqui porque é uma decisão de julgamento, não um
# número da spec.
_ENVELOPE_MARGIN = 1.30
_ENVELOPE_ROUND_MS = 50


def _percentile(valores: list[float], p: float) -> float:
    """p50/p95 por interpolação linear (`statistics.quantiles` faria discretização diferente)."""
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    if len(ordenados) == 1:
        return ordenados[0]
    posicao = (len(ordenados) - 1) * p
    baixo, alto = int(posicao), min(int(posicao) + 1, len(ordenados) - 1)
    fracao = posicao - baixo
    return ordenados[baixo] + (ordenados[alto] - ordenados[baixo]) * fracao


def _run_one(
    endpoint_name: str, contexto: dict[str, Any], max_tokens: int
) -> dict[str, Any]:
    try:
        resultado = inference.invoke(endpoint_name, contexto, max_tokens=max_tokens)
        return {
            "success": True,
            "latency_s": resultado.latency_s,
            "completion_tokens": resultado.completion_tokens or 0,
        }
    except (inference.InferenceProtocolError, inference.InferenceContentError) as exc:
        return {
            "success": False,
            "latency_s": None,
            "completion_tokens": 0,
            "error": str(exc),
        }


def _amostras_de_contexto() -> list[dict[str, Any]]:
    """Roda pelos contextos de `eval/cases.yaml` em rotação — nunca um único contexto fixo,
    para o benchmark não virar 100% hit de prompt cache do llama.cpp (dado real do probe
    do A2: prompt_cache_enabled=true muda a latência de um contexto repetido)."""
    return [caso["contexto"] for caso in evaluation.load_cases()]


def _freeze_envelope(p50_ms: float, p95_ms: float) -> dict[str, Any]:
    p95_max = (
        round((p95_ms * _ENVELOPE_MARGIN) / _ENVELOPE_ROUND_MS) * _ENVELOPE_ROUND_MS
    )
    return {
        "p50_typical_ms": round(p50_ms, 1),
        "p95_measured_ms": round(p95_ms, 1),
        "p95_max": p95_max,
        "margin_applied": _ENVELOPE_MARGIN,
        "rounding_ms": _ENVELOPE_ROUND_MS,
        "justificativa": (
            f"p95_max = round(p95_medido({p95_ms:.1f}ms) * {_ENVELOPE_MARGIN}) arredondado para "
            f"múltiplo de {_ENVELOPE_ROUND_MS}ms — margem de 30% sobre a variância normal de CPU "
            "compartilhada do Academy, congelada a partir de medição real (nunca de um número da "
            "spec/documentação)."
        ),
    }


def run_benchmark(
    release: str,
    *,
    endpoint_override: str | None,
    sequential: int,
    concurrent: int,
    max_tokens: int,
) -> dict[str, Any]:
    endpoint_name = endpoint_override or inference.resolve_endpoint_name(release)
    contextos = _amostras_de_contexto()

    def contexto_ciclico(i: int) -> dict[str, Any]:
        return contextos[i % len(contextos)]

    log(f"aquecendo {endpoint_name} com {WARMUP_REQUESTS} requisições (descartadas)...")
    for i in range(WARMUP_REQUESTS):
        _run_one(endpoint_name, contexto_ciclico(i), max_tokens)

    log(f"medindo {sequential} requisições sequenciais (max_tokens={max_tokens})...")
    sequenciais = [
        _run_one(endpoint_name, contexto_ciclico(i), max_tokens)
        for i in range(sequential)
    ]
    for i, r in enumerate(sequenciais):
        log(f"  [{'PASS' if r['success'] else 'FAIL'}] seq {i + 1}/{sequential}")

    concorrentes: list[dict[str, Any]] = []
    if concurrent:
        log(
            f"medindo lote concorrente ({concurrent} threads, informativo — não congela envelope)..."
        )
        with ThreadPoolExecutor(max_workers=concurrent) as pool:
            futuros = [
                pool.submit(_run_one, endpoint_name, contexto_ciclico(i), max_tokens)
                for i in range(concurrent)
            ]
            concorrentes = [f.result() for f in as_completed(futuros)]

    latencias = [r["latency_s"] * 1000 for r in sequenciais if r["success"]]
    sucesso = sum(1 for r in sequenciais if r["success"])
    total_tokens = sum(r["completion_tokens"] for r in sequenciais if r["success"])
    tempo_total_s = sum(r["latency_s"] for r in sequenciais if r["success"])

    p50_ms = _percentile(latencias, 0.50)
    p95_ms = _percentile(latencias, 0.95)
    tokens_por_s = (total_tokens / tempo_total_s) if tempo_total_s > 0 else 0.0

    manifest = hf_release.load_manifest(hf_release.manifest_path(release))
    envelope = _freeze_envelope(p50_ms, p95_ms) if latencias else None

    resultado = {
        "release": release,
        "endpoint_name": endpoint_name,
        "instance_type": manifest.get("instance_type"),
        "warmup_requests": WARMUP_REQUESTS,
        "sequential_requests": sequential,
        "concurrent_requests": concurrent,
        "max_tokens": max_tokens,
        "success_rate": round(sucesso / sequential, 3) if sequential else 0.0,
        "p50_ms": round(p50_ms, 1),
        "p95_ms": round(p95_ms, 1),
        "total_latency_s": round(
            sum(r["latency_s"] for r in sequenciais if r["success"]), 3
        ),
        "total_completion_tokens": total_tokens,
        "tokens_per_s": round(tokens_por_s, 2),
        "concurrent_success_rate": (
            round(sum(1 for r in concorrentes if r["success"]) / len(concorrentes), 3)
            if concorrentes
            else None
        ),
        "envelope_ms": envelope,
        "errors": [r["error"] for r in sequenciais if not r["success"]],
    }

    if envelope:
        inference.write_generated_runtime_patch(
            {"latency_envelope_ms": envelope, "release_measured": release}
        )

    existente = evidence.read("benchmark.json") or {}
    existente[release] = resultado
    evidence.write("benchmark.json", existente)

    ok = resultado["success_rate"] == 1.0
    log(
        f"{'[PASS]' if ok else '[FAIL]'} benchmark {release}: "
        f"success_rate={resultado['success_rate']:.0%} p50={p50_ms:.0f}ms p95={p95_ms:.0f}ms "
        f"tokens/s={tokens_por_s:.1f}"
    )
    return resultado


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", choices=["v1", "v2"], required=True)
    parser.add_argument("--endpoint", default=None)
    parser.add_argument(
        "--sequential",
        type=int,
        default=15,
        help="Requisições sequenciais medidas (10-20, default 15)",
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        default=0,
        help="Lote concorrente opcional (0 desliga; 2-4 se usado)",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=64, help="Tokens de saída (32-64, default 64)"
    )
    args = parser.parse_args()

    if not (10 <= args.sequential <= 20):
        parser.error("--sequential precisa estar entre 10 e 20 (spec §14)")
    if args.concurrent and not (2 <= args.concurrent <= 4):
        parser.error(
            "--concurrent precisa ser 0 (desligado) ou estar entre 2 e 4 (spec §14)"
        )
    if not (32 <= args.max_tokens <= 64):
        parser.error("--max-tokens precisa estar entre 32 e 64 (spec §14)")

    try:
        resultado = run_benchmark(
            args.release,
            endpoint_override=args.endpoint,
            sequential=args.sequential,
            concurrent=args.concurrent,
            max_tokens=args.max_tokens,
        )
    except LabError as exc:
        log(f"[FAIL] {exc}")
        return 1

    emit(resultado)
    return 0 if resultado["success_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
