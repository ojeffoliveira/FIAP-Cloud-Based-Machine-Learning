"""`make compare` -- evidencia lado a lado dos quatro candidatos de serving,
sem alterar infraestrutura e sem declarar vencedor.

Regra mais dura deste arquivo: a saida (JSON e Markdown) nunca pode conter
uma palavra que resolva a escolha pelo aluno -- nada de "recomendado",
"melhor", "vencedor", ranking, ordem de preferencia ou emoji de aprovacao.
O que existe aqui e numero ao lado de numero; a decisao e do aluno em
`student/DECISION.md`.

Por isso a amostra e pequena de proposito (ver `_SAMPLE_ROWS`): o objetivo e
mostrar o formato/protocolo de cada candidato, nao repetir a carga completa
dos workloads (isso e `workloads.py`, com os patterns ja escolhidos).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import serving
from .aws import log
from .config import Config

# Amostra pequena de proposito: o compare precisa terminar em minutos, nao
# repetir a carga inteira dos workloads (40/600 linhas -- isso e workloads.py).
_ATENDIMENTO_SAMPLE_ROWS = 5
_CAMPANHA_SAMPLE_ROWS = 20
_WARM_CALLS = 20
_PREDICTIONS_TOLERANCE = 1e-6

# Nao existe terraform output para o instance type do Batch Transform (ele
# nao e um recurso do Terraform -- ver model.tf). Mesmo valor comprovado no
# ACADEMY.md e no default de var.batch_instance_type, nunca um novo tipo.
_BATCH_INSTANCE_TYPE = "ml.m5.large"


def _predictions_match(a: list[float], b: list[float], tolerance: float = _PREDICTIONS_TOLERANCE) -> bool:
    if len(a) != len(b):
        return False
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def _compare_atendimento(cfg: Config, outputs: dict[str, Any], endpoint_status: dict[str, Any]) -> dict[str, Any]:
    ids, rows = serving.build_payload_rows(cfg.data_dir / "atendimento.csv", cfg)
    sample_rows = rows[:_ATENDIMENTO_SAMPLE_ROWS]
    body = "\n".join(sample_rows) + "\n"

    result: dict[str, Any] = {}
    probabilities_by_pattern: dict[str, list[float]] = {}

    for pattern, output_key, invoke in (
        ("realtime", "realtime_endpoint_name", serving.invoke_realtime),
        ("serverless", "serverless_endpoint_name", serving.invoke_serverless),
    ):
        endpoint_name = serving.require_output(outputs, output_key)
        log(f"[compare] atendimento/{pattern}: 1a chamada + {_WARM_CALLS} chamadas quentes em {endpoint_name}")

        first = invoke(endpoint_name, body, cfg.region)
        warm_samples_ms = []
        warm_probabilities = first.probabilities
        for _ in range(_WARM_CALLS):
            warm = invoke(endpoint_name, body, cfg.region)
            warm_samples_ms.append(warm.elapsed_ms)
            warm_probabilities = warm.probabilities
        probabilities_by_pattern[pattern] = warm_probabilities

        stats = serving.latency_stats(warm_samples_ms)
        status = endpoint_status.get(pattern, {})
        result[pattern] = {
            "endpoint_name": endpoint_name,
            "sample_rows": len(sample_rows),
            "first_ms": round(first.elapsed_ms, 3),
            "warm_calls": _WARM_CALLS,
            "warm_p50_ms": stats["p50_ms"],
            "warm_p95_ms": stats["p95_ms"],
            "current_instance_count": status.get("current_instance_count"),
            "capacity_model": (
                "instancia dedicada, sempre ligada" if pattern == "realtime" else "capacidade gerenciada pela AWS, sem instancia dedicada visivel"
            ),
        }

    result["predictions_match"] = _predictions_match(
        probabilities_by_pattern.get("realtime", []), probabilities_by_pattern.get("serverless", [])
    )
    result["tolerance"] = _PREDICTIONS_TOLERANCE
    result["sample_ids"] = ids[:_ATENDIMENTO_SAMPLE_ROWS]
    return result


def _compare_campanha(cfg: Config, outputs: dict[str, Any]) -> dict[str, Any]:
    ids, rows = serving.build_payload_rows(cfg.data_dir / "campaign_features.csv", cfg)
    sample_rows = rows[:_CAMPANHA_SAMPLE_ROWS]
    bucket = serving.require_output(outputs, "bucket_name")

    result: dict[str, Any] = {}

    endpoint_name = serving.require_output(outputs, "async_endpoint_name")
    log(f"[compare] campanha/async: submissao + polling de {len(sample_rows)} linhas em {endpoint_name}")
    started = time.monotonic()
    submission = serving.submit_async(endpoint_name, bucket, "final/async-input", sample_rows, cfg.region)
    async_result = serving.collect_async(submission, cfg.region, poll_seconds=5.0, timeout_seconds=300)
    result["async"] = {
        "endpoint_name": endpoint_name,
        "input_count": len(sample_rows),
        "output_count": len(async_result.probabilities),
        "duration_seconds": round(time.monotonic() - started, 3),
        "protocol": "InvokeEndpointAsync + polling do objeto de saida no S3 (sem notificacao SNS)",
        "persistent_infrastructure": "instancia do endpoint permanece alocada entre submissoes",
        "input_uri": submission.input_uri,
        "output_uri": submission.output_uri,
    }

    model_name = serving.require_output(outputs, "model_name")
    log(f"[compare] campanha/batch: transform job com {len(sample_rows)} linhas (instance_type={_BATCH_INSTANCE_TYPE})")
    started = time.monotonic()
    batch_result = serving.run_batch_transform(
        model_name,
        bucket,
        "final/batch-input",
        "final/batch-output",
        sample_rows,
        cfg.region,
        instance_type=_BATCH_INSTANCE_TYPE,
    )
    result["batch"] = {
        "job_name": batch_result.job_name,
        "input_count": len(sample_rows),
        "output_count": len(batch_result.probabilities),
        "duration_seconds": round(time.monotonic() - started, 3),
        "protocol": "CreateTransformJob (boto3, nao Terraform) + leitura do objeto unico gravado no prefixo de saida",
        "persistent_infrastructure": "job efemero -- a instancia existe so durante o processamento, e some depois",
        "output_uri": batch_result.output_uri,
    }

    result["sample_ids"] = ids[:_CAMPANHA_SAMPLE_ROWS]
    return result


def _write_json(cfg: Config, payload: dict[str, Any]) -> Path:
    out_path = cfg.evidence_dir / "candidates.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


def _write_markdown(cfg: Config, payload: dict[str, Any]) -> Path:
    atendimento = payload["atendimento"]
    campanha = payload["campanha"]
    lines: list[str] = []
    lines.append("# Candidatos de serving -- evidencia lado a lado")
    lines.append("")
    lines.append(
        "Este documento apresenta numeros de cada candidato, sem indicar qual "
        "escolher. A escolha e registrada pelo grupo em `student/DECISION.md`."
    )
    lines.append("")
    lines.append("## Atendimento: realtime x serverless")
    lines.append("")
    lines.append("| metrica | realtime | serverless |")
    lines.append("|---|---|---|")
    rt = atendimento["realtime"]
    sv = atendimento["serverless"]
    lines.append(f"| endpoint | {rt['endpoint_name']} | {sv['endpoint_name']} |")
    lines.append(f"| amostra (linhas) | {rt['sample_rows']} | {sv['sample_rows']} |")
    lines.append(f"| primeira chamada (ms) | {rt['first_ms']} | {sv['first_ms']} |")
    lines.append(f"| p50 aquecido (ms), {rt['warm_calls']} chamadas | {rt['warm_p50_ms']} | {sv['warm_p50_ms']} |")
    lines.append(f"| p95 aquecido (ms), {rt['warm_calls']} chamadas | {rt['warm_p95_ms']} | {sv['warm_p95_ms']} |")
    lines.append(
        f"| instancias correntes (API) | {rt['current_instance_count']} | {sv['current_instance_count']} |"
    )
    lines.append(f"| modelo de capacidade | {rt['capacity_model']} | {sv['capacity_model']} |")
    lines.append("")
    lines.append(
        f"Predicoes equivalentes entre os dois candidatos (tolerancia "
        f"{atendimento['tolerance']}): `{atendimento['predictions_match']}`."
    )
    lines.append("")
    lines.append("## Campanha: async x batch")
    lines.append("")
    lines.append("| metrica | async | batch |")
    lines.append("|---|---|---|")
    asy = campanha["async"]
    bat = campanha["batch"]
    lines.append(f"| linhas de entrada (amostra) | {asy['input_count']} | {bat['input_count']} |")
    lines.append(f"| linhas de saida | {asy['output_count']} | {bat['output_count']} |")
    lines.append(f"| tempo observado (s) | {asy['duration_seconds']} | {bat['duration_seconds']} |")
    lines.append(f"| protocolo de consumo | {asy['protocol']} | {bat['protocol']} |")
    lines.append(f"| infraestrutura | {asy['persistent_infrastructure']} | {bat['persistent_infrastructure']} |")
    lines.append("")
    lines.append(
        "Os numeros de tempo sao sobre uma amostra pequena, para caber no teto de "
        "tempo do curso -- nao sao um teste de carga e nao servem como criterio "
        "de aprovacao isolado. A leitura de protocolo (necessidade de polling, "
        "persistencia de infraestrutura) e tao parte da evidencia quanto o tempo."
    )
    lines.append("")

    out_path = cfg.evidence_dir / "candidates.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def cmd_compare(cfg: Config, args: Any) -> int:
    outputs = serving.terraform_outputs(cfg)
    endpoint_status = serving.collect_endpoint_status(cfg)

    payload = {
        "atendimento": _compare_atendimento(cfg, outputs, endpoint_status),
        "campanha": _compare_campanha(cfg, outputs),
    }

    json_path = _write_json(cfg, payload)
    md_path = _write_markdown(cfg, payload)
    log(f"[compare] evidencia escrita em {json_path} e {md_path}")
    log("[compare] este arquivo nao declara vencedor -- a escolha e do grupo em student/DECISION.md")

    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0
