"""Testes de `final_project.solution` — a superfície inteira de interação do
aluno com o trabalho final.

Cada teste de erro carrega duas garantias, não uma: (1) a mensagem é em
português e diz o que fazer; (2) a mensagem nunca insinua qual valor é o
"certo" — nem citando um pattern antes do outro como recomendação, nem usando
palavras de conselho ("recomenda", "melhor", "ideal", "deve escolher"). Sem
essa segunda garantia, o erro didático vira gabarito.
"""

from __future__ import annotations

import json

import pytest

from final_project.solution import (
    ATENDIMENTO_VALUES,
    CAMPANHA_VALUES,
    SolutionError,
    load_and_validate,
    load_solution,
    solution_path,
    write_evidence,
)

# Palavras que, se aparecerem numa mensagem de erro, indicam que o código
# está insinuando qual pattern escolher — o pecado capital deste módulo.
LEAKING_WORDS = (
    "recomend",
    "melhor",
    "ideal",
    "deve escolher",
    "sugerimos",
    "sugestão",
)

# Palavras cuja presença é evidência razoável de que a mensagem está em
# português (acentuação e vocabulário comuns nas mensagens de solution.py).
PORTUGUESE_HINTS = ("ã", "ç", "á", "é", "ó", "não", "está", "valores", "aceit")


def _assert_didactic_error(message: str) -> None:
    lowered = message.lower()
    assert any(hint in lowered for hint in PORTUGUESE_HINTS), (
        f"mensagem não parece PT-BR: {message!r}"
    )
    for leak in LEAKING_WORDS:
        assert leak not in lowered, (
            f"mensagem revela preferência de pattern ({leak!r}): {message!r}"
        )


def _write_yaml(path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _solution_yaml(
    *,
    atendimento: str = "TODO",
    campanha: str = "TODO",
    group: str = "Grupo Teste",
    members: tuple[str, ...] = ("Fulano da Silva (RM123456)",),
) -> str:
    if members:
        members_block = "\n".join(f'    - "{m}"' for m in members)
        authors_members = f"  members:\n{members_block}"
    else:
        authors_members = "  members: []"
    return (
        "version: 1\n"
        "serving:\n"
        "  atendimento:\n"
        f"    pattern: {atendimento}\n"
        "  campanha:\n"
        f"    pattern: {campanha}\n"
        "authors:\n"
        f'  group: "{group}"\n'
        f"{authors_members}\n"
    )


# --------------------------------------------------------------------------- #
# Arquivo ausente / YAML inválido
# --------------------------------------------------------------------------- #


def test_arquivo_ausente_gera_erro_didatico(minimal_config):
    path = solution_path(minimal_config)
    assert not path.exists()

    with pytest.raises(SolutionError) as excinfo:
        load_solution(path)

    message = str(excinfo.value)
    _assert_didactic_error(message)
    assert "make bootstrap" in message


def test_yaml_sintaticamente_invalido_gera_erro_didatico(tmp_path, minimal_config):
    path = minimal_config.root / "student" / "solution.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, "serving: [não fecha a lista\n  atendimento: realtime\n")

    with pytest.raises(SolutionError) as excinfo:
        load_solution(path)

    _assert_didactic_error(str(excinfo.value))


# --------------------------------------------------------------------------- #
# TODO ainda não preenchido
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "atendimento,campanha",
    [
        ("TODO", "async"),
        ("realtime", "TODO"),
        ("TODO", "TODO"),
    ],
)
def test_pattern_em_todo_gera_erro_didatico(minimal_config, atendimento, campanha):
    path = minimal_config.root / "student" / "solution.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, _solution_yaml(atendimento=atendimento, campanha=campanha))

    with pytest.raises(SolutionError) as excinfo:
        load_solution(path)

    _assert_didactic_error(str(excinfo.value))


# --------------------------------------------------------------------------- #
# Valor fora da lista de aceitos
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("valor_invalido", ["lambda", "ec2", ""])
def test_atendimento_com_valor_fora_da_lista(minimal_config, valor_invalido):
    path = minimal_config.root / "student" / "solution.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, _solution_yaml(atendimento=valor_invalido, campanha="async"))

    with pytest.raises(SolutionError) as excinfo:
        load_solution(path)

    _assert_didactic_error(str(excinfo.value))


@pytest.mark.parametrize("valor_invalido", ["lambda", "ec2", ""])
def test_campanha_com_valor_fora_da_lista(minimal_config, valor_invalido):
    path = minimal_config.root / "student" / "solution.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, _solution_yaml(atendimento="realtime", campanha=valor_invalido))

    with pytest.raises(SolutionError) as excinfo:
        load_solution(path)

    _assert_didactic_error(str(excinfo.value))


# --------------------------------------------------------------------------- #
# Pattern do outro workload (troca clássica: async/batch em atendimento,
# realtime/serverless em campanha)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("valor_trocado", ["batch", "async"])
def test_atendimento_com_valor_de_campanha_gera_erro_didatico(
    minimal_config, valor_trocado
):
    path = minimal_config.root / "student" / "solution.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, _solution_yaml(atendimento=valor_trocado, campanha="async"))

    with pytest.raises(SolutionError) as excinfo:
        load_solution(path)

    _assert_didactic_error(str(excinfo.value))


@pytest.mark.parametrize("valor_trocado", ["realtime", "serverless"])
def test_campanha_com_valor_de_atendimento_gera_erro_didatico(
    minimal_config, valor_trocado
):
    path = minimal_config.root / "student" / "solution.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, _solution_yaml(atendimento="realtime", campanha=valor_trocado))

    with pytest.raises(SolutionError) as excinfo:
        load_solution(path)

    _assert_didactic_error(str(excinfo.value))


# --------------------------------------------------------------------------- #
# Grupo e integrantes
# --------------------------------------------------------------------------- #


def test_grupo_em_branco_gera_erro_didatico(minimal_config):
    path = minimal_config.root / "student" / "solution.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(
        path, _solution_yaml(atendimento="realtime", campanha="async", group="   ")
    )

    with pytest.raises(SolutionError) as excinfo:
        load_solution(path)

    _assert_didactic_error(str(excinfo.value))


def test_lista_de_integrantes_vazia_gera_erro_didatico(minimal_config):
    path = minimal_config.root / "student" / "solution.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(
        path, _solution_yaml(atendimento="realtime", campanha="async", members=())
    )

    with pytest.raises(SolutionError) as excinfo:
        load_solution(path)

    _assert_didactic_error(str(excinfo.value))


# --------------------------------------------------------------------------- #
# Caso válido — as quatro combinações possíveis
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("atendimento", ATENDIMENTO_VALUES)
@pytest.mark.parametrize("campanha", CAMPANHA_VALUES)
def test_combinacao_valida_carrega_sem_erro(minimal_config, atendimento, campanha):
    path = minimal_config.root / "student" / "solution.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, _solution_yaml(atendimento=atendimento, campanha=campanha))

    solution = load_solution(path)

    assert solution.atendimento_pattern == atendimento
    assert solution.campanha_pattern == campanha
    assert solution.group == "Grupo Teste"
    assert solution.members == ["Fulano da Silva (RM123456)"]


# --------------------------------------------------------------------------- #
# SOLUTION_FILE do ambiente
# --------------------------------------------------------------------------- #


def test_solution_file_do_ambiente_sobrescreve_caminho_default(
    minimal_config, tmp_path, monkeypatch
):
    default_path = minimal_config.root / "student" / "solution.yaml"
    override_path = tmp_path / "outro-lugar" / "solucao-alternativa.yaml"
    override_path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("SOLUTION_FILE", str(override_path))
    assert solution_path(minimal_config) == override_path
    assert not default_path.exists()

    monkeypatch.delenv("SOLUTION_FILE", raising=False)
    assert solution_path(minimal_config) == default_path


# --------------------------------------------------------------------------- #
# Gravação da evidência
# --------------------------------------------------------------------------- #


def test_load_and_validate_grava_evidencia_solution_json(minimal_config, monkeypatch):
    path = minimal_config.root / "student" / "solution.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, _solution_yaml(atendimento="realtime", campanha="batch"))
    monkeypatch.delenv("SOLUTION_FILE", raising=False)

    solution = load_and_validate(minimal_config)

    evidence_path = minimal_config.evidence_dir / "solution.json"
    assert evidence_path.exists()

    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload["atendimento_pattern"] == "realtime"
    assert payload["campanha_pattern"] == "batch"
    assert payload["authors"]["group"] == "Grupo Teste"
    assert payload["authors"]["members"] == ["Fulano da Silva (RM123456)"]
    assert payload["source_path"] == str(solution.source_path)


def test_write_evidence_registra_hash_do_arquivo_fonte(minimal_config):
    path = minimal_config.root / "student" / "solution.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, _solution_yaml(atendimento="serverless", campanha="async"))

    solution = load_solution(path)
    out_path = write_evidence(minimal_config, solution)

    payload = json.loads(out_path.read_text(encoding="utf-8"))

    import hashlib

    assert payload["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
