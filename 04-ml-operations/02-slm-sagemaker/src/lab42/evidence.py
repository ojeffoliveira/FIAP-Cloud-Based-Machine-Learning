"""Dossiê de evidência do Lab 04.2 — cada afirmação aponta para arquivo e campo.

Mesmo princípio do 04.1: o lab não termina com alguém dizendo "funcionou".
Termina com um conjunto de arquivos em `artifacts/evidence/`, e cada frase do
`evidence.md` tem um JSON nomeado por trás.

Este módulo é a FONTE DE VERDADE do schema de cada arquivo de evidência do
lab (spec §14, 18 arquivos). Os agentes donos de cada estágio (A2, A5-A9)
escrevem seus próprios JSONs com `evidence.write(nome, payload)` — o formato
exigido de cada `payload` é o documentado em `STAGE_SCHEMA` abaixo; é contrato
congelado no Gate I, não sugestão.

`scripts/evidence.py` (dono: A0) é o único consumidor de `collect()` +
`render_markdown()` + `build_manifest()` — ele consolida o que existir e
reporta honestamente o que falta, sem fabricar valor.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import aws

SCHEMA_VERSION = "1.0.0"


def write(name: str, payload: dict[str, Any]) -> Path:
    """Escreve um arquivo de evidência e devolve o caminho. Nunca grava segredo."""
    aws.ensure_dirs()
    path = aws.EVIDENCE_DIR / name
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def read(name: str) -> dict[str, Any] | None:
    """Lê um arquivo de evidência já escrito, ou None se o estágio não rodou."""
    path = aws.EVIDENCE_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# Arquivo -> comando `make` que o gera (spec §14). Cada agente grava exatamente
# este nome de arquivo com `evidence.write(nome, payload)`. `manifest.json` e
# `evidence.md` são gerados por `scripts/evidence.py`, não têm produtor próprio.
STAGE_FILES: dict[str, str] = {
    "research-preflight.json": "make preflight",
    "model-v1.json": "make model-v1",
    "model-v2.json": "make model-v2",
    "credential-source-v2.json": "make pipeline-preflight (no runner, dentro do workflow deploy-v2)",
    "endpoint-v1.json": "make deploy-v1",
    "endpoint-v2.json": "workflow 04-2-deploy-v2.yml (apply V2)",
    "smoke-v1.json": "make smoke-v1",
    "smoke-v2.json": "workflow 04-2-deploy-v2.yml (smoke V2)",
    "evaluation-v1.json": "make quality",
    "evaluation-v2.json": "workflow 04-2-deploy-v2.yml (eval V2)",
    "benchmark.json": "make benchmark-v1 (e etapa de benchmark do workflow V2)",
    "autoscaling.json": "make autoscaling-status",
    "dashboard.json": "make deploy-v1 / make deploy-v2 (dashboard.tf)",
    "workflow-run.json": "workflow 04-2-deploy-v2.yml (self-report do run)",
    "release-recommendation.json": "make compare",
    "cleanup.json": "make destroy-all / make verify-clean",
}

# Campos mínimos exigidos em cada arquivo — contrato de schema congelado no
# Gate I. `render_markdown` só cita um campo que está listado aqui; adicionar
# campo novo é permitido, remover um destes quebra o consolidador.
STAGE_SCHEMA: dict[str, list[str]] = {
    "research-preflight.json": [
        "image_uri",
        "image_digest",
        "max_context_tokens",
        "max_output_tokens",
        "probe_status",
    ],
    "model-v1.json": [
        "repo",
        "revision",
        "filename",
        "sha256",
        "quantization",
        "s3_uri",
    ],
    "model-v2.json": [
        "repo",
        "revision",
        "filename",
        "sha256",
        "quantization",
        "s3_uri",
    ],
    "credential-source-v2.json": [
        "account",
        "arn_type",
        "credential_method",
        "static_env_keys_present",
    ],
    "endpoint-v1.json": [
        "endpoint_name",
        "endpoint_status",
        "instance_type",
        "release",
    ],
    "endpoint-v2.json": [
        "endpoint_name",
        "endpoint_status",
        "instance_type",
        "release",
    ],
    "smoke-v1.json": [
        "endpoint_name",
        "cases_total",
        "cases_passed",
        "protocol_errors",
    ],
    "smoke-v2.json": [
        "endpoint_name",
        "cases_total",
        "cases_passed",
        "protocol_errors",
    ],
    "evaluation-v1.json": ["cases_total", "cases_passed", "pii_violations"],
    "evaluation-v2.json": ["cases_total", "cases_passed", "pii_violations"],
    "benchmark.json": ["v1", "v2"],
    "autoscaling.json": [
        "resource_id",
        "min_capacity",
        "max_capacity",
        "policy_name",
        "target_value",
    ],
    "dashboard.json": ["dashboard_name", "widgets_count", "url"],
    "workflow-run.json": ["workflow", "run_id", "run_url", "conclusion", "actor"],
    "release-recommendation.json": ["recommended_release", "reasons"],
    "cleanup.json": ["verify_clean_passed", "resources_found", "checked_at"],
}


def collect() -> dict[str, dict[str, Any] | None]:
    """Lê todo arquivo de STAGE_FILES presente em artifacts/evidence/."""
    return {name: read(name) for name in STAGE_FILES}


def _campo(dados: dict[str, Any] | None, campo: str, arquivo: str, comando: str) -> str:
    if dados is None:
        return f"não executado nesta sessão (rode `{comando}`)"
    valor = dados.get(campo)
    if valor is None:
        return f"campo `{campo}` ausente em `{arquivo}`"
    return str(valor)


def render_markdown(
    collected: dict[str, dict[str, Any] | None], *, conta: str, regiao: str
) -> str:
    """Monta o evidence.md. Toda linha cita o arquivo (e o campo, quando aplicável)."""
    linhas: list[str] = [
        "# Evidência — Lab 04.2 · SLM no SageMaker + entrega contínua segura",
        "",
        f"Gerado em {datetime.now(timezone.utc).isoformat()} · conta {conta} · região {regiao}",
        "",
        (
            "Toda afirmação abaixo tem um arquivo e um campo verificável nesta mesma pasta. "
            "O que não rodou nesta sessão aparece marcado como pendente — nunca preenchido "
            "com um valor inventado."
        ),
        "",
    ]

    secoes: list[tuple[str, str, list[str]]] = [
        (
            "1. Preflight de runtime",
            "research-preflight.json",
            [
                "image_uri",
                "image_digest",
                "max_context_tokens",
                "max_output_tokens",
                "probe_status",
            ],
        ),
        (
            "2. Release V1 (manual)",
            "model-v1.json",
            ["repo", "revision", "filename", "sha256", "quantization", "s3_uri"],
        ),
        (
            "3. Release V2 (pipeline)",
            "model-v2.json",
            ["repo", "revision", "filename", "sha256", "quantization", "s3_uri"],
        ),
        (
            "4. Fonte de credencial do deploy V2",
            "credential-source-v2.json",
            ["account", "arn_type", "credential_method", "static_env_keys_present"],
        ),
        (
            "5. Endpoint V1",
            "endpoint-v1.json",
            ["endpoint_name", "endpoint_status", "instance_type", "release"],
        ),
        (
            "6. Endpoint V2",
            "endpoint-v2.json",
            ["endpoint_name", "endpoint_status", "instance_type", "release"],
        ),
        (
            "7. Smoke V1",
            "smoke-v1.json",
            ["endpoint_name", "cases_total", "cases_passed", "protocol_errors"],
        ),
        (
            "8. Smoke V2",
            "smoke-v2.json",
            ["endpoint_name", "cases_total", "cases_passed", "protocol_errors"],
        ),
        (
            "9. Evaluation V1",
            "evaluation-v1.json",
            ["cases_total", "cases_passed", "pii_violations"],
        ),
        (
            "10. Evaluation V2",
            "evaluation-v2.json",
            ["cases_total", "cases_passed", "pii_violations"],
        ),
        ("11. Benchmark", "benchmark.json", ["v1", "v2"]),
        (
            "12. Autoscaling V2",
            "autoscaling.json",
            [
                "resource_id",
                "min_capacity",
                "max_capacity",
                "policy_name",
                "target_value",
            ],
        ),
        ("13. Dashboard", "dashboard.json", ["dashboard_name", "widgets_count", "url"]),
        (
            "14. Execução do workflow de deploy",
            "workflow-run.json",
            ["workflow", "run_id", "run_url", "conclusion", "actor"],
        ),
        (
            "15. Recomendação de release",
            "release-recommendation.json",
            ["recommended_release", "reasons"],
        ),
        (
            "16. Limpeza",
            "cleanup.json",
            ["verify_clean_passed", "resources_found", "checked_at"],
        ),
    ]

    for titulo, arquivo, campos in secoes:
        comando = STAGE_FILES[arquivo]
        dados = collected.get(arquivo)
        linhas += [
            f"## {titulo}",
            "",
            "| Campo | Valor | Onde conferir |",
            "|---|---|---|",
        ]
        for campo in campos:
            valor = _campo(dados, campo, arquivo, comando)
            linhas.append(f"| `{campo}` | {valor} | `{arquivo}` → `{campo}` |")
        linhas.append("")

    linhas += [
        "## O que este dossiê NÃO prova",
        "",
        (
            "- Não prova que V2 é estritamente melhor que V1 só por ter quantização "
            "diferente — a comparação em `release-recommendation.json` precisa justificar "
            "com números, não com a versão do arquivo."
        ),
        (
            "- Não prova segurança do pipeline além do que `credential-source-v2.json` e "
            "`workflow-run.json` registram — não substitui uma revisão manual do workflow."
        ),
        (
            "- Não prova qualidade de conversa real com o atendente da Bora Fibra: o "
            "`eval/cases.yaml` é sintético, criado para este lab."
        ),
        "- Não substitui a decisão humana registrada em `DECISION.md`.",
        "",
    ]
    return "\n".join(linhas) + "\n"


def build_manifest(
    collected: dict[str, dict[str, Any] | None], *, conta: str, regiao: str
) -> dict[str, Any]:
    """Índice do dossiê: o que existe, o que faltou, e qual comando gera o que falta."""
    presentes = {
        arquivo: {"existe": dados is not None, "gerado_por": STAGE_FILES[arquivo]}
        for arquivo, dados in collected.items()
    }
    faltando = [arquivo for arquivo, dados in collected.items() if dados is None]

    return {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "conta": conta,
        "regiao": regiao,
        "arquivos_por_estagio": presentes,
        "arquivos_faltando": faltando,
        "completo": not faltando,
        "arquivos_consolidados": ["manifest.json", "evidence.md"],
    }
