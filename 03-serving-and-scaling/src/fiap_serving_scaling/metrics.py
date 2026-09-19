"""Estatísticas de latência e o executor concorrente de teste de carga do `make load`."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import boto3

from fiap_serving_scaling.aws import client


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def latency_stats(samples_ms: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(percentile(samples_ms, 50), 3),
        "p95_ms": round(percentile(samples_ms, 95), 3),
        "p99_ms": round(percentile(samples_ms, 99), 3),
    }


def _single_invoke(session: boto3.session.Session, endpoint_name: str, body: str) -> tuple[bool, float]:
    runtime = client(session, "sagemaker-runtime")
    started = time.monotonic()
    try:
        runtime.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="text/csv",
            Accept="text/csv",
            Body=body.encode("utf-8"),
        )
        return True, (time.monotonic() - started) * 1000.0
    except Exception:  # noqa: BLE001 - chamada que falha ou toma throttling é dado, não crash
        return False, (time.monotonic() - started) * 1000.0


def run_load_level(
    session: boto3.session.Session,
    endpoint_name: str,
    body: str,
    concurrency: int,
    requests: int,
    duration_s: float = 0.0,
) -> dict[str, Any]:
    latencies: list[float] = []
    successes = 0
    feitas = 0
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        if duration_s > 0:
            # Modo "tem que dar para ver no painel": em vez de um número fixo de
            # requisições, o nível ocupa uma janela de tempo. Submete em ondas do
            # tamanho da concorrência até a janela fechar, então a concorrência
            # instantânea continua sendo exatamente `concurrency` — o que muda é a
            # duração, não o perfil de carga.
            deadline = started + duration_s
            while time.monotonic() < deadline:
                onda = [pool.submit(_single_invoke, session, endpoint_name, body) for _ in range(concurrency)]
                for future in as_completed(onda):
                    ok, elapsed_ms = future.result()
                    latencies.append(elapsed_ms)
                    feitas += 1
                    if ok:
                        successes += 1
        else:
            feitas = requests
            futures = [pool.submit(_single_invoke, session, endpoint_name, body) for _ in range(requests)]
            for future in as_completed(futures):
                ok, elapsed_ms = future.result()
                latencies.append(elapsed_ms)
                if ok:
                    successes += 1
    wall_seconds = max(time.monotonic() - started, 1e-6)
    return {
        "concurrency": concurrency,
        "requests": feitas,
        "successes": successes,
        "success_rate": round(successes / feitas, 4) if feitas else 0.0,
        "requests_per_second": round(feitas / wall_seconds, 2),
        **latency_stats(latencies),
    }


class TrafegoDeFundo:
    """Mantém chamadas num endpoint enquanto outra coisa acontece.

    Existe por causa do painel: o `make scale-demo` sobe a capacidade sem gerar
    nenhuma chamada, então os gráficos que dependem de invocação (total e por
    instância) ficam sem dado exatamente no minuto em que a segunda instância
    entra — que é o minuto interessante. Com tráfego ao fundo, a série "por
    instância" cai para perto da metade do total quando a segunda máquina começa
    a atender, e a distribuição fica visível em vez de apenas afirmada.
    """

    def __init__(self, session: boto3.session.Session, endpoint_name: str, body: str, rps: float = 1.0) -> None:
        self._session = session
        self._endpoint_name = endpoint_name
        self._body = body
        self._intervalo = 1.0 / max(rps, 0.01)
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self.chamadas = 0
        self.falhas = 0

    def _laco(self) -> None:
        while not self._parar.is_set():
            ok, _ = _single_invoke(self._session, self._endpoint_name, self._body)
            self.chamadas += 1
            if not ok:
                self.falhas += 1
            self._parar.wait(self._intervalo)

    def __enter__(self) -> TrafegoDeFundo:
        self._thread = threading.Thread(target=self._laco, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._parar.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
