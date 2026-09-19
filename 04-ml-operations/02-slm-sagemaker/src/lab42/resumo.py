"""Frases legíveis a partir das evidências e regravação do bloco no `DECISION.md`.

Mesma mecânica do Lab 03 (`03-serving-and-scaling/scripts/lab.py`): o aluno não
transcreve número de JSON. `scripts/resumo.py` lê os arquivos que existem em
`artifacts/evidence/`, monta uma frase por linha (arredondada, em português de
gente) e regrava só o bloco entre os marcadores do `DECISION.md` — o resto do
documento, escrito pelo aluno, nunca é tocado.

Este módulo cobre as seis evidências citadas pelo `DECISION.md` deste lab:
fumaça de V1, qualidade (gate generativo) de V1 e V2, desempenho V1×V2 e as
duas provas de segurança do pipeline V2 (credencial e execução do workflow).
As outras evidências do dossiê (`autoscaling.json`, `dashboard.json` etc.) têm
seção própria no `evidence.md` gerado por `make evidence` — não duplicam aqui.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .aws import EVIDENCE_DIR, LAB_ROOT, log

DECISION_PATH = LAB_ROOT / "DECISION.md"

INICIO_EVIDENCIAS = "<!-- inicio-evidencias -->"
FIM_EVIDENCIAS = "<!-- fim-evidencias -->"

# chave -> (rótulo da linha, arquivo, comando que gera o arquivo)
_LINHAS: list[tuple[str, str, str, str]] = [
    ("fumaca_v1", "Fumaça V1", "smoke-v1.json", "make smoke-v1"),
    ("qualidade_v1", "Qualidade V1 (gate generativo)", "evaluation-v1.json", "make quality"),
    (
        "qualidade_v2",
        "Qualidade V2 (gate generativo)",
        "evaluation-v2.json",
        "workflow `04-2-deploy-v2.yml`",
    ),
    (
        "desempenho",
        "Desempenho V1 × V2 (latência e tokens/s)",
        "benchmark.json",
        "make benchmark-v1",
    ),
    (
        "credencial_v2",
        "Credencial do deploy V2",
        "credential-source-v2.json",
        "make pipeline-preflight (dentro do workflow)",
    ),
    (
        "workflow_v2",
        "Execução do workflow V2",
        "workflow-run.json",
        "workflow `04-2-deploy-v2.yml`",
    ),
]


def _le_evidencia(nome: str) -> dict[str, Any] | None:
    caminho = EVIDENCE_DIR / nome
    if not caminho.exists():
        return None
    import json

    with caminho.open(encoding="utf-8") as handle:
        return json.load(handle)


def _tempo(ms: float | None) -> str:
    """Tempo em linguagem de gente, não em campo de JSON.

    Arredonda (a terceira casa decimal é ruído de rede, não sinal) e troca para
    segundos acima de mil milissegundos — ninguém lê "2581 ms" como "dois
    segundos e meio", e é essa percepção que decide se o envelope de latência
    serve para uma recomendação durante uma ligação (spec-visual §4).
    """
    if ms is None:
        return "não medido"
    if ms >= 1000:
        return f"{ms / 1000:.1f}".replace(".", ",") + " segundos"
    return f"{round(ms)} ms"


def _tokens_por_s(valor: float | None) -> str:
    if valor is None:
        return "não medido"
    return f"{valor:.1f}".replace(".", ",")


def _frase_fumaca(dados: dict[str, Any] | None) -> str | None:
    if not dados:
        return None
    total, aprovados = dados.get("cases_total"), dados.get("cases_passed")
    erros = dados.get("protocol_errors") or []
    frase = f"**{aprovados}/{total}** casos de fumaça responderam corretamente no endpoint V1"
    frase += "." if not erros else f", com {len(erros)} erro(s) de protocolo."
    return frase


def _frase_qualidade(dados: dict[str, Any] | None, release: str) -> str | None:
    if not dados:
        return None
    total, aprovados = dados.get("cases_total"), dados.get("cases_passed")
    pii = dados.get("pii_violations", 0)
    frase = (
        f"**{aprovados}/{total}** casos passaram no gate generativo de {release} "
        f"(idioma, tamanho, contrato de saída), com **{pii}** violação(ões) de PII "
        "ou valor inventado."
    )
    return frase


def _frase_desempenho(dados: dict[str, Any] | None) -> str | None:
    if not dados:
        return None
    v1 = dados.get("v1")
    v2 = dados.get("v2")
    if not v1:
        return None
    frase = (
        f"V1: metade das respostas saiu em até **{_tempo(v1.get('p50_ms'))}**, "
        f"95% em até **{_tempo(v1.get('p95_ms'))}**, a **{_tokens_por_s(v1.get('tokens_per_s'))} "
        "tokens/s**."
    )
    if v2:
        frase += (
            f" V2: metade em até **{_tempo(v2.get('p50_ms'))}**, 95% em até "
            f"**{_tempo(v2.get('p95_ms'))}**, a **{_tokens_por_s(v2.get('tokens_per_s'))} "
            "tokens/s**."
        )
        p50_v1, p50_v2 = v1.get("p50_ms"), v2.get("p50_ms")
        if p50_v1 and p50_v2:
            razao = p50_v2 / p50_v1
            if razao >= 1.05:
                frase += f" V2 respondeu cerca de {razao:.1f}x mais lento que V1 (quantização mais pesada)."
            elif razao <= 0.95:
                frase += f" V2 respondeu cerca de {1 / razao:.1f}x mais rápido que V1."
            else:
                frase += " As duas releases respondem em tempo praticamente igual."
    else:
        frase += " V2 ainda não tem benchmark próprio — a chave `v2` do arquivo nasce no workflow de deploy V2."
    return frase


def _frase_credencial(dados: dict[str, Any] | None) -> str | None:
    if not dados:
        return None
    metodo = dados.get("credential_method")
    tipo = dados.get("arn_type")
    chave_estatica = dados.get("static_env_keys_present")
    frase = f"A credencial do deploy V2 veio de **{metodo}** (tipo `{tipo}`)"
    if chave_estatica:
        frase += ", e havia chave estática AWS presente no ambiente — quebra a regra dura do lab."
    else:
        frase += ", sem nenhuma chave estática AWS no ambiente."
    return frase


def _frase_workflow(dados: dict[str, Any] | None) -> str | None:
    if not dados:
        return None
    workflow = dados.get("workflow")
    run_id = dados.get("run_id")
    conclusao = dados.get("conclusion")
    ator = dados.get("actor")
    return (
        f"O workflow `{workflow}` (run **{run_id}**, disparado por **{ator}**) "
        f"terminou com conclusão **{conclusao}**."
    )


def frases_evidencia() -> dict[str, str]:
    """Lê todas as evidências existentes e devolve uma frase por linha da tabela."""
    smoke_v1 = _le_evidencia("smoke-v1.json")
    eval_v1 = _le_evidencia("evaluation-v1.json")
    eval_v2 = _le_evidencia("evaluation-v2.json")
    benchmark = _le_evidencia("benchmark.json")
    credencial_v2 = _le_evidencia("credential-source-v2.json")
    workflow_v2 = _le_evidencia("workflow-run.json")

    candidatas = {
        "fumaca_v1": _frase_fumaca(smoke_v1),
        "qualidade_v1": _frase_qualidade(eval_v1, "V1"),
        "qualidade_v2": _frase_qualidade(eval_v2, "V2"),
        "desempenho": _frase_desempenho(benchmark),
        "credencial_v2": _frase_credencial(credencial_v2),
        "workflow_v2": _frase_workflow(workflow_v2),
    }
    return {chave: frase for chave, frase in candidatas.items() if frase}


def _grava_evidencias_no_decision(frases: dict[str, str]) -> int:
    """Reescreve a tabela de evidências do DECISION.md com os números medidos.

    Só o bloco entre os marcadores é trocado: as seções de recomendação escritas
    pelo aluno ficam intactas, e rodar de novo não duplica nada. Se os marcadores
    não existirem (apagados sem querer), avisa e não mexe — perder o texto do
    aluno é muito pior que deixar a tabela desatualizada (spec-visual §3).
    """
    if not DECISION_PATH.exists():
        log("aviso: DECISION.md não encontrado; a tabela não foi atualizada")
        return 0

    texto = DECISION_PATH.read_text(encoding="utf-8")
    if INICIO_EVIDENCIAS not in texto or FIM_EVIDENCIAS not in texto:
        log("aviso: os marcadores de evidência não estão no DECISION.md; a tabela não foi atualizada")
        return 0

    bloco = [
        INICIO_EVIDENCIAS,
        "| O que valida | Evidência | O que medimos na sua execução |",
        "|---|---|---|",
    ]
    for chave, rotulo, arquivo, comando in _LINHAS:
        valor = frases.get(chave, f"_rode {comando}_")
        bloco.append(f"| {rotulo} | `{arquivo}` | {valor} |")
    bloco.append(FIM_EVIDENCIAS)

    novo_texto = (
        texto[: texto.index(INICIO_EVIDENCIAS)]
        + "\n".join(bloco)
        + texto[texto.index(FIM_EVIDENCIAS) + len(FIM_EVIDENCIAS) :]
    )
    DECISION_PATH.write_text(novo_texto, encoding="utf-8")
    return len(frases)


def run() -> dict[str, Any]:
    """Imprime as frases no terminal (stderr) e regrava a tabela do DECISION.md.

    As mesmas frases vão para os dois lugares, de propósito: se o terminal
    dissesse uma coisa e o documento outra, o aluno não saberia em qual confiar.
    """
    frases = frases_evidencia()

    for chave, rotulo, _arquivo, comando in _LINHAS:
        log(rotulo)
        if chave in frases:
            # O negrito do markdown não ajuda no terminal; sai só no documento.
            log("  " + frases[chave].replace("**", ""))
        else:
            log(f"  ainda não medido — rode {comando}")
        log("")

    escritas = _grava_evidencias_no_decision(frases)
    log(f"DECISION.md: tabela de evidências atualizada ({escritas} de {len(_LINHAS)} linhas com dado medido).")
    log("O que resta no arquivo é só a sua decisão — a tabela é regravada a cada `make resumo`.")

    return frases
