"""Parser e validador de `student/solution.yaml` — o quebra-cabeça declarativo
que o aluno de fato edita.

Este módulo é a peça central da experiência do aluno no trabalho final: toda
mensagem de erro precisa dizer exatamente o que está errado e quais são os
valores aceitos, **sem nunca insinuar qual valor escolher**. `make run` e
`make validate-solution` (scripts/final.py, entregue por outro agente) chamam
só `load_and_validate(cfg)`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import Config

ATENDIMENTO_VALUES: tuple[str, ...] = ("realtime", "serverless")
CAMPANHA_VALUES: tuple[str, ...] = ("async", "batch")


class SolutionError(RuntimeError):
    """Erro didático do solution.yaml: a mensagem diz o que corrigir, nunca a resposta certa."""


@dataclass(frozen=True)
class Solution:
    atendimento_pattern: str
    campanha_pattern: str
    group: str
    members: list[str]
    source_path: Path


def solution_path(cfg: Config) -> Path:
    """Caminho do solution.yaml. Respeita `SOLUTION_FILE` do ambiente — o
    override que a validação de referência do professor usa para rodar o
    mesmo pipeline sem tocar no arquivo do aluno."""
    override = os.environ.get("SOLUTION_FILE")
    if override:
        return Path(override)
    return cfg.root / "student" / "solution.yaml"


def _yaml_error_location(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return ""
    return f" (linha {mark.line + 1}, coluna {mark.column + 1})"


def _extract_pattern(
    serving: dict[str, Any],
    *,
    workload: str,
    accepted: tuple[str, ...],
    other_workload: str,
    other_accepted: tuple[str, ...],
    path: Path,
) -> str:
    section = serving.get(workload)
    if not isinstance(section, dict) or "pattern" not in section:
        raise SolutionError(
            f"{path}: faltou `serving.{workload}.pattern`. Valores aceitos para "
            f"{workload}: {', '.join(accepted)}. Restaure a seção a partir do "
            "template gerado por `make bootstrap` se ela foi removida por engano."
        )

    raw_value = section.get("pattern")
    value = str(raw_value).strip() if raw_value is not None else ""

    if value == "" or value.upper() == "TODO":
        raise SolutionError(
            f"{path}: `serving.{workload}.pattern` ainda está em TODO. Escolha um "
            f"dos valores aceitos para {workload}: {', '.join(accepted)}."
        )

    if value in other_accepted and value not in accepted:
        raise SolutionError(
            f"{path}: `serving.{workload}.pattern: {value}` é um valor do workload "
            f"`{other_workload}`, não de `{workload}`. {workload.capitalize()} "
            f"aceita só: {', '.join(accepted)}."
        )

    if value not in accepted:
        raise SolutionError(
            f"{path}: `serving.{workload}.pattern: {value}` não é um valor aceito. "
            f"Valores aceitos para {workload}: {', '.join(accepted)}."
        )

    return value


def load_solution(path: Path) -> Solution:
    """Lê e valida `path`. Levanta `SolutionError` com mensagem didática em
    português no primeiro problema encontrado; nunca sugere qual pattern
    escolher."""
    if not path.exists():
        raise SolutionError(
            f"arquivo de solução não encontrado em {path}.\n"
            "Rode `make bootstrap`: ele cria o template em student/solution.yaml. "
            "Edite só as duas linhas `pattern` e os dados do grupo."
        )

    raw_text = path.read_text(encoding="utf-8")

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise SolutionError(
            f"{path} não é um YAML válido{_yaml_error_location(exc)}: {exc}\n"
            "Corrija a sintaxe (indentação, dois-pontos, aspas) e mantenha a "
            "estrutura do template original."
        ) from exc

    if not isinstance(data, dict):
        raise SolutionError(
            f"{path} precisa ser um mapeamento YAML (chave: valor), não "
            f"{type(data).__name__}. Restaure a partir do template de `make bootstrap`."
        )

    serving = data.get("serving")
    if not isinstance(serving, dict):
        raise SolutionError(
            f"{path}: faltou a seção `serving:` com `atendimento` e `campanha`. "
            "Restaure a partir do template gerado por `make bootstrap`."
        )

    atendimento_pattern = _extract_pattern(
        serving,
        workload="atendimento",
        accepted=ATENDIMENTO_VALUES,
        other_workload="campanha",
        other_accepted=CAMPANHA_VALUES,
        path=path,
    )
    campanha_pattern = _extract_pattern(
        serving,
        workload="campanha",
        accepted=CAMPANHA_VALUES,
        other_workload="atendimento",
        other_accepted=ATENDIMENTO_VALUES,
        path=path,
    )

    authors = data.get("authors")
    if not isinstance(authors, dict):
        raise SolutionError(
            f"{path}: faltou a seção `authors:` com `group` e `members`. "
            "Restaure a partir do template gerado por `make bootstrap`."
        )

    group = str(authors.get("group") or "").strip()
    if not group:
        raise SolutionError(
            f"{path}: `authors.group` está em branco. Preencha com o nome do "
            "grupo antes de rodar `make run`."
        )

    members_raw = authors.get("members")
    members = (
        [str(member).strip() for member in members_raw]
        if isinstance(members_raw, list)
        else []
    )
    members = [member for member in members if member]
    if not members:
        raise SolutionError(
            f"{path}: `authors.members` está em branco ou não é uma lista. "
            "Liste ao menos um integrante do grupo, um por item."
        )

    return Solution(
        atendimento_pattern=atendimento_pattern,
        campanha_pattern=campanha_pattern,
        group=group,
        members=members,
        source_path=path,
    )


def write_evidence(cfg: Config, solution: Solution) -> Path:
    """Grava artifacts/evidence/solution.json com os patterns escolhidos e um
    hash do arquivo fonte — prova de qual solution.yaml gerou a execução,
    sem duplicar o conteúdo inteiro do arquivo do aluno na evidência."""
    digest = hashlib.sha256(solution.source_path.read_bytes()).hexdigest()
    payload = {
        "source_path": str(solution.source_path),
        "sha256": digest,
        "atendimento_pattern": solution.atendimento_pattern,
        "campanha_pattern": solution.campanha_pattern,
        "authors": {"group": solution.group, "members": solution.members},
    }
    out_path = cfg.evidence_dir / "solution.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


def load_and_validate(cfg: Config) -> Solution:
    """Ponto de entrada único para o CLI: carrega, valida e grava a evidência.
    Usado por `validate-solution` e como primeiro passo de `run`."""
    solution = load_solution(solution_path(cfg))
    write_evidence(cfg, solution)
    return solution
