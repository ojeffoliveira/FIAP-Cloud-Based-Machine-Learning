"""Testes de contrato do evaluation set do SLM (`eval/cases.yaml`).

O conteúdo de avaliação (casos, prompts, `src/lab42/evaluation.py`) é
entregável do agente A8 e já existe no repositório: esta suíte valida o
**contrato** definido em `02_SPEC_LAB04_2_SLM_CICD.md` §8 — schema de cada
caso, ausência de PII no contexto sintético, e a regra de que o gate
generativo nunca compara texto exato — contra o conteúdo real de
`eval/cases.yaml`, não mais contra um placeholder ausente.
"""

import pathlib
import re

import pytest
import yaml

LAB_ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES_PATH = LAB_ROOT / "eval" / "cases.yaml"

REQUIRED_CASE_FIELDS = {"id", "contexto", "checks"}
REQUIRED_CHECK_FIELDS = {"idioma", "deve_mencionar_algum", "nao_deve_inventar"}

# O contrato de entrada do SLM (spec §3) é sintético e sem PII por desenho:
# nunca deveria haver nome, CPF, telefone, e-mail ou endereço no contexto
# de um caso de avaliação.
PII_FIELD_PATTERNS = re.compile(
    r"(?i)\b(nome|cpf|telefone|celular|e-?mail|endereco|endereço)\b"
)
PII_VALUE_PATTERNS = (
    re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),  # CPF formatado
    re.compile(r"\b\d{11}\b"),  # CPF sem pontuação
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),  # e-mail
)


def _load_cases():
    if not CASES_PATH.exists():
        pytest.fail(f"evaluation set obrigatório ausente: {CASES_PATH}")
    with CASES_PATH.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, list):
        pytest.fail(
            f"{CASES_PATH.name}: esperado uma lista de casos no topo, encontrado {type(doc)}"
        )
    return doc


def test_entre_6_e_10_casos():
    """Reprova um evaluation set fora do tamanho definido pela spec §8 —
    poucos casos não cobrem cenários suficientes, muitos casos deixam o
    smoke/eval do pipeline lento demais para o budget de aula."""
    casos = _load_cases()
    assert 6 <= len(casos) <= 10, (
        f"eval/cases.yaml tem {len(casos)} casos, esperado entre 6 e 10"
    )


def test_ids_unicos():
    """Reprova ids duplicados — o pipeline de evaluation e o benchmark
    referenciam casos por id; um id repetido faz um caso mascarar outro
    silenciosamente no relatório de evidence."""
    casos = _load_cases()
    ids = [caso.get("id") for caso in casos]
    duplicados = {i for i in ids if ids.count(i) > 1}
    assert not duplicados, f"ids duplicados em eval/cases.yaml: {duplicados}"


def test_schema_de_cada_caso():
    """Reprova qualquer caso sem os três campos do contrato (id, contexto,
    checks) — scripts/evaluate.py precisa poder assumir esse schema sem
    validação defensiva espalhada pelo código."""
    casos = _load_cases()
    for caso in casos:
        faltando = REQUIRED_CASE_FIELDS - caso.keys()
        assert not faltando, (
            f"caso {caso.get('id', '?')} sem campos obrigatórios: {faltando}"
        )


def test_checks_tem_campos_obrigatorios():
    """Reprova checks sem idioma/deve_mencionar_algum/nao_deve_inventar —
    são os três sinais que sustentam o gate generativo sem comparar texto
    exato (spec §8)."""
    casos = _load_cases()
    for caso in casos:
        checks = caso.get("checks", {})
        faltando = REQUIRED_CHECK_FIELDS - checks.keys()
        assert not faltando, (
            f"caso {caso.get('id', '?')}: checks sem campos obrigatórios: {faltando}"
        )


def test_checks_idioma_pt_br():
    """Reprova qualquer caso cujo idioma esperado não seja pt-BR — o
    contrato de saída do SLM é sempre português (spec §3), então o
    evaluation set não deveria testar outro idioma."""
    casos = _load_cases()
    for caso in casos:
        idioma = caso.get("checks", {}).get("idioma")
        assert idioma == "pt-BR", (
            f"caso {caso.get('id', '?')}: checks.idioma={idioma!r}, esperado 'pt-BR'"
        )


def test_contexto_sem_campos_de_pii():
    """Reprova qualquer caso cujo contexto sintético contenha nome, CPF,
    telefone, e-mail ou endereço — o SLM só recebe contexto sintético e
    sem PII por desenho (spec §3); um caso de eval com PII validaria um
    cenário que a arquitetura proíbe."""
    casos = _load_cases()
    achados = []
    for caso in casos:
        contexto = caso.get("contexto", {})
        for chave, valor in contexto.items():
            if PII_FIELD_PATTERNS.search(str(chave)):
                achados.append((caso.get("id", "?"), f"chave suspeita: {chave}"))
            for padrao in PII_VALUE_PATTERNS:
                if padrao.search(str(valor)):
                    achados.append(
                        (caso.get("id", "?"), f"valor suspeito em {chave}: {valor!r}")
                    )
    assert not achados, f"PII encontrada em contexto de caso de eval: {achados}"


def test_nenhum_check_compara_texto_exato():
    """Reprova qualquer check que pareça comparação de texto exato (chave
    tipo resposta_esperada/texto_esperado/output_esperado, ou valor de
    deve_mencionar_algum/nao_deve_inventar com frase completa em vez de
    palavra/termo curto) — o gate generativo do lab é deliberadamente não
    determinístico byte-a-byte (spec §8: 'não comparar texto exato')."""
    casos = _load_cases()
    chaves_proibidas = {
        "resposta_exata",
        "texto_esperado",
        "output_esperado",
        "resposta_esperada",
    }
    achados = []
    for caso in casos:
        checks = caso.get("checks", {})
        presentes = chaves_proibidas & checks.keys()
        if presentes:
            achados.append((caso.get("id", "?"), presentes))
        for lista_chave in ("deve_mencionar_algum", "nao_deve_inventar"):
            for termo in checks.get(lista_chave, []) or []:
                # termo com mais de 6 palavras é sinal de frase completa,
                # não palavra/conceito-chave — heurística deliberadamente
                # frouxa para não gerar falso positivo em termos compostos.
                if len(str(termo).split()) > 6:
                    achados.append(
                        (
                            caso.get("id", "?"),
                            f"{lista_chave} parece frase completa: {termo!r}",
                        )
                    )
    assert not achados, f"Check de comparação de texto exato encontrado: {achados}"
