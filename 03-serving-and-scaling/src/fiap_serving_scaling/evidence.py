"""Consolida o dossiê de evidências em artifacts/evidence/ a partir dos resultados
JSON que cada comando já escreveu, mais um snapshot novo em resources.json puxado
direto das APIs da AWS (nunca do state do Terraform).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import boto3

from fiap_serving_scaling.aws import (
    describe_endpoint,
    describe_endpoint_config,
    describe_scalable_targets,
    describe_scaling_policies,
    describe_training_job,
    json_safe,
)
from fiap_serving_scaling.config import log


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def build_resources_snapshot(session: boto3.session.Session, outputs: dict[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}

    training_job_name = outputs.get("training_job_name")
    if training_job_name:
        job = describe_training_job(session, training_job_name)
        snapshot["training_job"] = {
            "name": training_job_name,
            "status": job["TrainingJobStatus"],
            "billable_seconds": job.get("BillableTimeInSeconds"),
            "artifact_uri": job.get("ModelArtifacts", {}).get("S3ModelArtifacts"),
        }

    for mode, name_key in (
        ("realtime", "realtime_endpoint_name"),
        ("serverless", "serverless_endpoint_name"),
        ("async", "async_endpoint_name"),
    ):
        name = outputs.get(name_key)
        if not name:
            continue
        endpoint = describe_endpoint(session, name)
        config_name = endpoint["EndpointConfigName"]
        config = describe_endpoint_config(session, config_name)
        snapshot[f"{mode}_endpoint"] = {
            "name": name,
            "status": endpoint["EndpointStatus"],
            "endpoint_config_name": config_name,
            "production_variants": json_safe(config.get("ProductionVariants", [])),
        }

    for mode, resource_id_key in (
        ("realtime", "realtime_scalable_resource_id"),
        ("async", "async_scalable_resource_id"),
    ):
        resource_id = outputs.get(resource_id_key)
        if not resource_id:
            continue
        targets = describe_scalable_targets(session, resource_id)
        policies = describe_scaling_policies(session, resource_id)
        snapshot[f"{mode}_scaling"] = {
            "resource_id": resource_id,
            "scalable_targets": json_safe(targets),
            "policy_names": [p["PolicyName"] for p in policies],
        }

    return snapshot


def build_summary(evidence_dir: Path, resources: dict[str, Any]) -> dict[str, Any]:
    compare = _read_json_if_exists(evidence_dir / "compare.json")
    async_result = _read_json_if_exists(evidence_dir / "async.json")
    batch = _read_json_if_exists(evidence_dir / "batch.json")
    load = _read_json_if_exists(evidence_dir / "load.json")
    scale = _read_json_if_exists(evidence_dir / "scale.json")

    checks = {
        "training_completed": resources.get("training_job", {}).get("status") == "Completed",
        "artifact_uri_from_describe_training_job": bool(
            resources.get("training_job", {}).get("artifact_uri")
        ),
        "realtime_in_service": resources.get("realtime_endpoint", {}).get("status") == "InService",
        "serverless_in_service": resources.get("serverless_endpoint", {}).get("status") == "InService",
        "async_in_service": resources.get("async_endpoint", {}).get("status") == "InService",
        "predictions_match_realtime_serverless": bool(
            compare and compare.get("predictions_match")
        ),
        "async_output_count_matches_input": bool(
            async_result and async_result.get("output_count") == async_result.get("input_count")
        ),
        "batch_produced_600_predictions": bool(batch and batch.get("output_count") == 600),
        "load_success_rate_over_threshold": bool(
            load and all(level["success_rate"] >= 0.99 for level in load.get("levels", []))
        ),
        "scale_demo_proved_1_2_1": bool(
            scale
            and scale.get("before") == 1
            and scale.get("scaled") == 2
            and scale.get("restored") == 1
        ),
    }

    summary = {
        "chain_complete": all(checks.values()),
        "checks": checks,
        "resources": resources,
        "compare": compare,
        "async": async_result,
        "batch": batch,
        "load": load,
        "scale": scale,
    }
    return summary


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Evidências — Lab 03 Serving and Scaling",
        "",
        "Um modelo, quatro contratos de serving. Cada linha abaixo é conferível "
        "contra uma chamada de API da AWS ao vivo, não contra um print.",
        "",
        "## Cadeia",
        "",
        "| Elo | Status |",
        "|---|---|",
    ]
    labels = {
        "training_completed": "Training bootstrap concluído",
        "artifact_uri_from_describe_training_job": "URI do artefato veio do DescribeTrainingJob",
        "realtime_in_service": "Real-time endpoint InService",
        "serverless_in_service": "Serverless endpoint InService",
        "async_in_service": "Async endpoint InService",
        "predictions_match_realtime_serverless": "Predições equivalentes entre real-time e serverless",
        "async_output_count_matches_input": "Async: contagem de saída == contagem de entrada",
        "batch_produced_600_predictions": "Batch Transform: 600 predições",
        "load_success_rate_over_threshold": "Teste de carga: taxa de sucesso >= 99% em todos os níveis",
        "scale_demo_proved_1_2_1": "Scale-demo: 1 -> 2 -> 1 provado pela API",
    }
    for key, label in labels.items():
        mark = "PASS" if summary["checks"].get(key) else "FAIL"
        lines.append(f"| {label} | {mark} |")

    lines += [
        "",
        f"**Cadeia completa: {'sim' if summary['chain_complete'] else 'não'}**",
        "",
    ]
    return "\n".join(lines)


def write_evidence(evidence_dir: Path, summary: dict[str, Any]) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    with open(evidence_dir / "summary.json", "w", encoding="utf-8") as handle:
        json.dump(json_safe(summary), handle, indent=2, sort_keys=True)
    with open(evidence_dir / "summary.md", "w", encoding="utf-8") as handle:
        handle.write(render_markdown(summary))
    log(f"[evidence] escrevi {evidence_dir / 'summary.json'} e summary.md")
