"""Estatísticas de latência e o executor concorrente de teste de carga do `make load`."""

from __future__ import annotations

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
) -> dict[str, Any]:
    latencies: list[float] = []
    successes = 0
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_single_invoke, session, endpoint_name, body) for _ in range(requests)]
        for future in as_completed(futures):
            ok, elapsed_ms = future.result()
            latencies.append(elapsed_ms)
            if ok:
                successes += 1
    wall_seconds = max(time.monotonic() - started, 1e-6)
    return {
        "concurrency": concurrency,
        "requests": requests,
        "successes": successes,
        "success_rate": round(successes / requests, 4) if requests else 0.0,
        "requests_per_second": round(requests / wall_seconds, 2),
        **latency_stats(latencies),
    }
