"""Leitura de config/scenario.yaml e config/acceptance.yaml, e resolução dos
caminhos do trabalho final.

Ponto único de acesso à configuração: nenhum outro módulo declara `open()`
sobre esses dois YAML. Isso evita duas verdades sobre a mesma coisa — a ordem
das features, o namespace do CloudWatch, o limiar de PSI — cada uma vivendo em
um arquivo diferente e saindo de sincronia depois de uma edição em só um deles.

`config/scenario.yaml` e `config/acceptance.yaml` são entregues por outro
agente (dataset/calibração). As chaves exatas que este módulo espera estão
documentadas em `/tmp/tf-final/shared/PEDIDOS.md`; o essencial é: os valores
que o contrato do trabalho final já congela (seed, ordem das features, nome do
target/id, namespace, limiar de PSI) vêm de `scenario.yaml`, e os números que
dependem de calibração real (gates de PSI/F1/ROC-AUC de baseline e shifted)
vêm de `acceptance.yaml`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    """Falha acionável de configuração: a mensagem diz o que corrigir."""


# Raiz do projeto = três níveis acima deste arquivo:
# src/final_project/config.py -> final_project -> src -> 05-final-project.
_DEFAULT_ROOT = Path(__file__).resolve().parents[2]

# Nome do grupo/aluno usado em todo recurso Terraform, via local.prefix.
# Espelha var.student_id em terraform/variables.tf: os dois lados leem o
# mesmo TF_VAR_student_id para nunca ficarem com prefixos diferentes.
_STUDENT_ID_ENV_VARS = ("TF_VAR_student_id", "STUDENT_ID")
_DEFAULT_STUDENT_ID = "aluno"

# Região fixa por restrição do AWS Academy (não é decisão de configuração).
_DEFAULT_REGION = "us-east-1"


@dataclass(frozen=True)
class Config:
    """Configuração resolvida do trabalho final. Imutável: um módulo que
    precisar de um valor diferente cria outra instância, nunca muta esta."""

    # --- caminhos ---------------------------------------------------------
    root: Path
    artifacts_dir: Path
    data_dir: Path
    evidence_dir: Path
    generated_dir: Path
    terraform_dir: Path

    # --- identidade e AWS ---------------------------------------------------
    region: str
    student_id: str
    prefix: str

    # --- contrato do dataset (congelado, ver 04_DATASET_AND_ACCEPTANCE.md) --
    seed: int
    feature_order: list[str]
    label_column: str
    id_column: str

    # --- observabilidade -----------------------------------------------------
    psi_threshold: float
    namespace: str

    # --- dados brutos dos dois YAML, para quem precisar de uma chave que
    # ainda não foi promovida a atributo explícito acima. ---------------------
    scenario: dict[str, Any] = field(default_factory=dict)
    acceptance: dict[str, Any] = field(default_factory=dict)


def _student_id_from_env() -> str:
    for env_var in _STUDENT_ID_ENV_VARS:
        value = os.environ.get(env_var)
        if value:
            return value
    return _DEFAULT_STUDENT_ID


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(
            f"arquivo de configuração ausente: {path}. Ele é entregue junto do "
            "dataset do trabalho final — se você está vendo isto antes disso "
            "existir, rode os módulos que dependem de config.load_config() só "
            "depois de config/scenario.yaml e config/acceptance.yaml existirem."
        )
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ConfigError(f"{path} precisa ser um mapeamento YAML (chave: valor).")
    return data


def _require(mapping: dict[str, Any], *keys: str, source: Path) -> Any:
    """Navega chaves aninhadas (`_require(d, "a", "b")` == `d["a"]["b"]`) com
    erro didático em vez de KeyError cru."""
    current: Any = mapping
    trail: list[str] = []
    for key in keys:
        trail.append(key)
        if not isinstance(current, dict) or key not in current:
            raise ConfigError(
                f"{source}: chave `{'.'.join(trail)}` ausente ou mal formada. "
                "Veja as chaves esperadas em /tmp/tf-final/shared/PEDIDOS.md."
            )
        current = current[key]
    return current


def load_config(root: Path | None = None) -> Config:
    """Carrega scenario.yaml + acceptance.yaml e resolve os caminhos do
    trabalho final. Cria os diretórios de artifacts/.generated quando ainda
    não existem — o resto do código pode escrever neles sem checar antes."""
    project_root = (root or _DEFAULT_ROOT).resolve()
    config_dir = project_root / "config"

    scenario = _read_yaml(config_dir / "scenario.yaml")
    acceptance = _read_yaml(config_dir / "acceptance.yaml")

    student_id = _student_id_from_env()

    artifacts_dir = project_root / "artifacts"
    data_dir = artifacts_dir / "data"
    evidence_dir = artifacts_dir / "evidence"
    generated_dir = project_root / ".generated"
    terraform_dir = project_root / "terraform"

    for path in (artifacts_dir, data_dir, evidence_dir, generated_dir):
        path.mkdir(parents=True, exist_ok=True)

    feature_order = list(_require(scenario, "dataset", "feature_order", source=config_dir / "scenario.yaml"))
    label_column = str(_require(scenario, "dataset", "label", source=config_dir / "scenario.yaml"))
    id_column = str(_require(scenario, "dataset", "id_column", source=config_dir / "scenario.yaml"))
    seed = int(_require(scenario, "seed", source=config_dir / "scenario.yaml"))
    namespace = str(_require(scenario, "monitoring", "namespace", source=config_dir / "scenario.yaml"))
    psi_threshold = float(_require(scenario, "monitoring", "psi_threshold", source=config_dir / "scenario.yaml"))

    return Config(
        root=project_root,
        artifacts_dir=artifacts_dir,
        data_dir=data_dir,
        evidence_dir=evidence_dir,
        generated_dir=generated_dir,
        terraform_dir=terraform_dir,
        region=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or _DEFAULT_REGION,
        student_id=student_id,
        prefix=f"fiap-final-{student_id}",
        seed=seed,
        feature_order=feature_order,
        label_column=label_column,
        id_column=id_column,
        psi_threshold=psi_threshold,
        namespace=namespace,
        scenario=scenario,
        acceptance=acceptance,
    )
