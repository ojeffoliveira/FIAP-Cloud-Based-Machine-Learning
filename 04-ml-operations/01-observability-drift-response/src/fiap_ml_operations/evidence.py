"""Dossiê de evidência — cada afirmação aponta para um arquivo e um campo.

O princípio: o lab não termina com alguém dizendo "funcionou". Termina com um
conjunto de arquivos em que cada frase do relatório tem um JSON por trás, e cada
JSON veio de uma chamada de API, não de uma variável do próprio script.

Os arquivos por etapa (`baseline-drift.json`, `production-drift.json`,
`alarm.json`, `reaction.json`, `quality.json`) são escritos pelos próprios comandos
enquanto rodam. O `make evidence` acrescenta o que só faz sentido consultar ao
final — estado dos recursos, contagem de datapoints, dashboard — e escreve o
`evidence.md` amarrando tudo.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

SCHEMA_VERSION = "1.0.0"


def write(name: str, payload: dict[str, Any]) -> Path:
    """Escreve um arquivo de evidência e devolve o caminho."""
    config.ensure_dirs()
    path = config.EVIDENCE_DIR / name
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def read(name: str) -> dict[str, Any] | None:
    """Lê um arquivo de evidência já escrito, ou None se a etapa não rodou."""
    path = config.EVIDENCE_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


STAGE_FILES = {
    "baseline-drift.json": "make baseline",
    "production-drift.json": "make drift",
    "alarm.json": "make alarm-status",
    "reaction.json": "make reaction",
    "quality.json": "make ground-truth",
}


def _fmt(value: Any, digits: int = 4) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.{digits}f}" if isinstance(value, float) else str(value)
    return str(value)


def _linha_pendente(comando: str) -> str:
    return f"não executado nesta sessão (rode `{comando}`)"


def _resumir(texto: str, limite: int = 150) -> str:
    """Encurta um texto longo sem cortar palavra no meio.

    O `StateReason` do CloudWatch passa de 200 caracteres e estouraria a coluna da
    tabela; cortar em posição fixa deixava a frase terminando em "equal to t", que
    parece defeito do relatório em vez de abreviação.
    """
    texto = str(texto).strip()
    if len(texto) <= limite:
        return texto
    corte = texto[:limite].rsplit(" ", 1)[0]
    return f"{corte} […]"


def render_markdown(bundle: dict[str, Any]) -> str:
    """Monta o evidence.md. Cada linha da tabela cita arquivo e campo."""
    recursos = bundle.get("recursos") or {}
    dashboard = bundle.get("dashboard") or {}
    baseline = bundle.get("baseline_drift") or {}
    producao = bundle.get("production_drift") or {}
    alarme = bundle.get("alarme") or {}
    reacao = bundle.get("reacao") or {}
    qualidade = bundle.get("qualidade") or {}
    cloudwatch = bundle.get("cloudwatch") or {}

    linhas: list[str] = [
        "# Evidência — Lab 04.1 · Observabilidade, drift e resposta operacional",
        "",
        f"Gerado em {bundle['gerado_em']} · conta {bundle['conta']} · região {bundle['regiao']}",
        "",
        "Capacidade de negócio: **"
        + str(bundle.get("linhagem", {}).get("business_capability", "?"))
        + "** · linhagem do modelo: **"
        + str(bundle.get("linhagem", {}).get("model_lineage", "?"))
        + "** · origem do contrato de dados: **"
        + str(bundle.get("linhagem", {}).get("source_lab", "?"))
        + "**",
        "",
        "Toda afirmação abaixo tem um arquivo e um campo verificável nesta mesma pasta.",
        "",
        "## 1. A infraestrutura estava saudável",
        "",
        "| Afirmação | Valor observado | Onde conferir |",
        "|---|---|---|",
        f"| O endpoint existia e estava InService | {recursos.get('endpoint_status', '?')} | `resource-status.json` → `endpoint_status` |",
        f"| O modelo em operação era a linhagem churn-v1 | {recursos.get('model_lineage', '?')} | `resource-status.json` → `model_lineage` |",
        f"| Nenhum erro HTTP no período | 4XX={cloudwatch.get('invocation_4xx', '?')} / 5XX={cloudwatch.get('invocation_5xx', '?')} | `cloudwatch.json` → `invocation_4xx`, `invocation_5xx` |",
        f"| O endpoint atendeu chamadas | {cloudwatch.get('invocations', '?')} invocações | `cloudwatch.json` → `invocations` |",
        "",
        "> Estas quatro linhas são exatamente o que um painel de infraestrutura mostraria.",
        "> Nenhuma delas diz se o modelo continua certo.",
        "",
        "## 2. Os dados mudaram",
        "",
        "| Janela | PSI máximo | Feature responsável | Features acima do limiar | Onde conferir |",
        "|---|---|---|---|---|",
    ]

    for rotulo, dados, arquivo in (
        ("baseline", baseline, "baseline-drift.json"),
        ("com drift", producao, "production-drift.json"),
    ):
        if not dados:
            linhas.append(f"| {rotulo} | {_linha_pendente(STAGE_FILES[arquivo])} | — | — | `{arquivo}` |")
            continue
        linhas.append(
            f"| {rotulo} | {_fmt(dados.get('psi_max'))} | "
            f"`{dados.get('feature_com_maior_psi', '?')}` | "
            f"{len(dados.get('features_acima_do_limiar', []))} | `{arquivo}` → `psi_max` |"
        )

    linhas += [
        "",
        "## 3. As predições mudaram",
        "",
        "| Janela | PSI das predições | Taxa de churn prevista | Probabilidade média | Onde conferir |",
        "|---|---|---|---|---|",
    ]
    for rotulo, dados, arquivo in (
        ("baseline", baseline, "baseline-drift.json"),
        ("com drift", producao, "production-drift.json"),
    ):
        if not dados:
            linhas.append(f"| {rotulo} | {_linha_pendente(STAGE_FILES[arquivo])} | — | — | `{arquivo}` |")
            continue
        linhas.append(
            f"| {rotulo} | {_fmt(dados.get('prediction_psi'))} | "
            f"{_fmt(dados.get('predicted_churn_rate'))} | "
            f"{_fmt(dados.get('mean_churn_probability'))} | `{arquivo}` → `prediction_psi` |"
        )

    linhas += [
        "",
        "## 4. A regra virou incidente",
        "",
        "| Afirmação | Valor observado | Onde conferir |",
        "|---|---|---|",
    ]
    if alarme:
        linhas += [
            f"| O alarme chegou a ALARM | {alarme.get('estado', '?')} | `alarm.json` → `estado` |",
            f"| O limiar avaliado | {_fmt(alarme.get('limiar'))} | `alarm.json` → `limiar` |",
            f"| Motivo registrado pelo CloudWatch | {_resumir(alarme.get('motivo', ''))} | `alarm.json` → `motivo` |",
        ]
    else:
        linhas.append(f"| Estado do alarme | {_linha_pendente('make alarm-status')} | `alarm.json` |")

    linhas += [
        "",
        "## 5. O sistema reagiu — e o que ele deliberadamente NÃO fez",
        "",
        "| Afirmação | Valor observado | Onde conferir |",
        "|---|---|---|",
    ]
    if reacao:
        incidente = reacao.get("incidente") or {}
        linhas += [
            f"| A Lambda foi invocada pelo EventBridge | {reacao.get('invocacoes_no_log', '?')} invocação(ões) no log | `reaction.json` → `invocacoes_no_log` |",
            f"| Um incidente foi escrito no S3 | `{reacao.get('objeto_s3', '?')}` | `reaction.json` → `objeto_s3` |",
            f"| A decisão registrada | {incidente.get('status', '?')} / {incidente.get('recommended_action', '?')} | `reaction.json` → `incidente.status` |",
            f"| Não houve training job novo | {reacao.get('training_jobs_criados_depois', '?')} job(s) criado(s) após o alarme | `reaction.json` → `training_jobs_criados_depois` |",
            f"| ReactionTriggered apareceu no CloudWatch | {cloudwatch.get('reaction_triggered_datapoints', '?')} datapoint(s) | `cloudwatch.json` → `reaction_triggered_datapoints` |",
        ]
    else:
        linhas.append(f"| Reação | {_linha_pendente('make reaction')} | `reaction.json` |")

    linhas += [
        "",
        "## 6. A mudança virou perda de qualidade",
        "",
    ]
    if qualidade:
        comparacao = qualidade.get("comparacao") or {}
        linhas += [
            "| Janela | F1 | ROC-AUC | Churn previsto | Churn real | Onde conferir |",
            "|---|---|---|---|---|---|",
        ]
        for chave, rotulo in (("baseline", "baseline"), ("drift", "com drift")):
            janela = (qualidade.get("janelas") or {}).get(chave) or {}
            if not janela:
                continue
            linhas.append(
                f"| {rotulo} | {_fmt(janela.get('f1'))} | {_fmt(janela.get('roc_auc'))} | "
                f"{_fmt(janela.get('predicted_churn_rate'))} | "
                f"{_fmt(janela.get('actual_churn_rate'))} | `quality.json` → `janelas.{chave}` |"
            )
        linhas += [
            "",
            f"Queda de F1 medida: **{_fmt(comparacao.get('queda_f1'))}** · "
            f"queda de ROC-AUC: **{_fmt(comparacao.get('queda_roc_auc'))}**",
            "",
            f"Modo de falha na janela com drift: {comparacao.get('modo_de_falha', '?')}",
            "",
            f"Leitura: {comparacao.get('leitura', '?')}",
        ]
    else:
        linhas.append(f"Qualidade: {_linha_pendente('make ground-truth')} — veja `quality.json`.")

    linhas += [
        "",
        "## 7. O dashboard existia e estava populado",
        "",
        "| Afirmação | Valor observado | Onde conferir |",
        "|---|---|---|",
        f"| O dashboard foi criado por Terraform | `{dashboard.get('nome', '?')}` | `dashboard.json` → `nome` |",
        f"| Quantidade de widgets | {dashboard.get('widgets', '?')} | `dashboard.json` → `widgets` |",
        f"| Datapoints de DataDriftPSIMax na última hora | {cloudwatch.get('data_drift_datapoints', '?')} | `cloudwatch.json` → `data_drift_datapoints` |",
        "",
        "## 8. O que este dossiê NÃO prova",
        "",
        "- Não prova que retreinar resolveria: nenhuma churn-v2 foi treinada neste lab.",
        "- Não prova causa: o PSI mostra que a distribuição mudou, não por que mudou.",
        "- Não prova generalização: as janelas têm 200 linhas cada, tamanho de aula.",
        "- Não substitui a decisão humana registrada em `DECISION.md`.",
        "",
    ]
    return "\n".join(linhas) + "\n"


def build_manifest(bundle: dict[str, Any]) -> dict[str, Any]:
    """Índice do dossiê: o que existe, o que faltou e por que."""
    presentes = {}
    for arquivo, comando in STAGE_FILES.items():
        caminho = config.EVIDENCE_DIR / arquivo
        presentes[arquivo] = {
            "existe": caminho.exists(),
            "gerado_por": comando,
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "conta": bundle.get("conta"),
        "regiao": bundle.get("regiao"),
        "linhagem": bundle.get("linhagem"),
        "arquivos_por_etapa": presentes,
        "arquivos_consolidados": [
            "resource-status.json",
            "cloudwatch.json",
            "dashboard.json",
            "evidence.md",
            "manifest.json",
        ],
    }
