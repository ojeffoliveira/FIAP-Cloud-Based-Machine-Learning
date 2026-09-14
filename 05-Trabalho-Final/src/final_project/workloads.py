"""`make run` -- executa os dois workloads do aluno usando so os patterns
escolhidos em `student/solution.yaml`. Nunca chama Terraform, nunca cria
infraestrutura: resolve nomes via `serving.terraform_outputs` (leitura do
estado ja aplicado) e invoca so o adapter do pattern escolhido -- e por isso
que trocar as duas linhas do YAML e rodar `make run` de novo nunca dispara
`apply` nem treino de novo.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import serving
from .aws import log
from .config import Config
from .solution import load_and_validate

# Mesmo valor do Terraform (var.batch_instance_type) e do ACADEMY.md -- o
# Batch Transform nao e um recurso do Terraform, entao nao existe output
# para isso; o candidato "batch" precisa do instance type mesmo assim.
_BATCH_INSTANCE_TYPE = "ml.m5.large"


def _write_evidence(cfg: Config, name: str, payload: dict[str, Any]) -> Path:
    out_path = cfg.evidence_dir / name
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


def _risk_sanity_check(cfg: Config, ids: list[str], probabilities: list[float]) -> None:
    """Confere que o modelo separa risco alto de risco baixo nos 40 clientes
    fixos do atendimento -- nao e um gate (nao derruba `cmd_atendimento`),
    e um aviso em stderr para o aluno notar um modelo quebrado antes de
    interpretar latencia como se fosse o unico numero que importa."""
    profiles = cfg.scenario.get("atendimento", {}).get("profiles", [])
    if not profiles:
        return

    ranges: dict[str, tuple[int, int]] = {}
    start = 0
    for profile in profiles:
        count = int(profile["count"])
        ranges[profile["name"]] = (start, start + count)
        start += count

    def _avg(name: str) -> float | None:
        bounds = ranges.get(name)
        if bounds is None:
            return None
        lo, hi = bounds
        window = probabilities[lo:hi]
        return sum(window) / len(window) if window else None

    high_avg = _avg("high_risk")
    low_avg = _avg("low_risk")
    if high_avg is None or low_avg is None:
        return

    log(f"[atendimento] media de probabilidade: high_risk={high_avg:.4f} low_risk={low_avg:.4f}")
    if high_avg <= low_avg:
        log(
            "[atendimento] aviso: a media de probabilidade do perfil high_risk nao "
            "ficou acima da do low_risk -- vale checar o modelo antes de interpretar "
            "os outros numeros deste workload."
        )


def cmd_atendimento(cfg: Config, args: Any) -> int:
    solution = load_and_validate(cfg)
    pattern = solution.atendimento_pattern
    outputs = serving.terraform_outputs(cfg)

    if pattern == "realtime":
        endpoint_name = serving.require_output(outputs, "realtime_endpoint_name")
        invoke = serving.invoke_realtime
    else:
        endpoint_name = serving.require_output(outputs, "serverless_endpoint_name")
        invoke = serving.invoke_serverless

    log(f"[atendimento] pattern escolhido: {pattern} ({endpoint_name})")
    ids, rows = serving.build_payload_rows(cfg.data_dir / "atendimento.csv", cfg)
    log(f"[atendimento] {len(rows)} clientes, uma chamada por vez (contrato sincrono)")

    probabilities: list[float] = []
    latencies_ms: list[float] = []
    errors = 0
    for index, row in enumerate(rows):
        try:
            result = invoke(endpoint_name, row, cfg.region)
            probabilities.append(result.probabilities[0])
            latencies_ms.append(result.elapsed_ms)
        except Exception as exc:  # dado, nunca crash do workload -- 40 chamadas independentes
            errors += 1
            probabilities.append(float("nan"))
            log(f"[atendimento] cliente {ids[index] if index < len(ids) else index} falhou: {exc}")

    _risk_sanity_check(cfg, ids, [p for p in probabilities if p == p])  # filtra NaN

    finite = [p for p in probabilities if p == p and 0.0 <= p <= 1.0]
    success_rate = len(finite) / len(rows) if rows else 0.0
    stats = serving.latency_stats(latencies_ms[1:]) if len(latencies_ms) > 1 else {"p50_ms": 0.0, "p95_ms": 0.0}

    payload = {
        "pattern": pattern,
        "request_count": len(rows),
        "success_rate": round(success_rate, 4),
        "first_ms": round(latencies_ms[0], 3) if latencies_ms else 0.0,
        "warm_p50_ms": stats["p50_ms"],
        "warm_p95_ms": stats["p95_ms"],
        "synchronous_contract": True,
        "notes": (
            f"{errors} falha(s) em {len(rows)} chamadas sincronas (uma por vez), "
            f"pattern={pattern}. p95 nao e usado como gate de aprovacao aqui, so registro."
        ),
    }
    atendimento_path = _write_evidence(cfg, "atendimento.json", payload)

    status = serving.collect_endpoint_status(cfg)
    serving_path = _write_evidence(cfg, "serving.json", status)
    log(f"[atendimento] evidencia em {atendimento_path} e {serving_path}")

    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if success_rate == 1.0 else 1


def cmd_campanha(cfg: Config, args: Any) -> int:
    solution = load_and_validate(cfg)
    pattern = solution.campanha_pattern
    outputs = serving.terraform_outputs(cfg)
    bucket = serving.require_output(outputs, "bucket_name")

    ids, rows = serving.build_payload_rows(cfg.data_dir / "campaign_features.csv", cfg)
    log(f"[campanha] pattern escolhido: {pattern}, {len(rows)} clientes")

    started = time.monotonic()
    if pattern == "async":
        endpoint_name = serving.require_output(outputs, "async_endpoint_name")
        log(f"[campanha] submetendo lote assincrono para {endpoint_name}")
        submission = serving.submit_async(endpoint_name, bucket, "final/campanha-async-input", rows, cfg.region)
        async_result = serving.collect_async(submission, cfg.region, poll_seconds=15.0, timeout_seconds=1800)
        probabilities = async_result.probabilities
        persistent_capacity_required = True
        notes = (
            "async: instancia do endpoint permanece alocada, saida consumida por "
            "polling do objeto no S3 (sem SNS)."
        )
    else:
        model_name = serving.require_output(outputs, "model_name")
        log(f"[campanha] criando transform job com model {model_name}")
        batch_result = serving.run_batch_transform(
            model_name,
            bucket,
            "final/campanha-batch-input",
            "final/campanha-batch-output",
            rows,
            cfg.region,
            instance_type=_BATCH_INSTANCE_TYPE,
        )
        probabilities = batch_result.probabilities
        persistent_capacity_required = False
        notes = "batch: job efemero (boto3, nao Terraform), instancia existe so durante o processamento."

    duration_seconds = time.monotonic() - started

    payload = {
        "pattern": pattern,
        "input_count": len(rows),
        "output_count": len(probabilities),
        "duration_seconds": round(duration_seconds, 3),
        "persistent_capacity_required": persistent_capacity_required,
        "notes": notes,
    }
    campanha_path = _write_evidence(cfg, "campanha.json", payload)

    status = serving.collect_endpoint_status(cfg)
    serving_path = _write_evidence(cfg, "serving.json", status)
    log(f"[campanha] evidencia em {campanha_path} e {serving_path}")

    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if payload["output_count"] == len(rows) else 1
