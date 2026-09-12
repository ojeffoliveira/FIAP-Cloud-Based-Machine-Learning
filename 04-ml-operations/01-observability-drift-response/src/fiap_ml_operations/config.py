"""Leitura do config/lab.yaml e resolução de caminhos do laboratório.

Um único ponto de acesso ao arquivo de configuração: qualquer módulo que precise
de uma constante do lab pede aqui, para não existirem duas verdades sobre a mesma
coisa (ex.: a ordem das features).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

LAB_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = LAB_ROOT / "config" / "lab.yaml"
ARTIFACTS_DIR = LAB_ROOT / "artifacts"
DATA_DIR = ARTIFACTS_DIR / "data"
PREDICTIONS_DIR = ARTIFACTS_DIR / "predictions"
EVIDENCE_DIR = ARTIFACTS_DIR / "evidence"
TERRAFORM_DIR = LAB_ROOT / "terraform"


@functools.lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Devolve o config/lab.yaml já parseado. Cacheado: é lido muitas vezes."""
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def feature_order() -> list[str]:
    """Ordem canônica das features, herdada do Lab 02.

    É a única ordem em que o payload pode ser serializado: o XGBoost recebe CSV
    sem cabeçalho, então posição é significado.
    """
    return list(load_config()["dataset"]["feature_order"])


def label_column() -> str:
    return str(load_config()["dataset"]["label"])


def id_column() -> str:
    return str(load_config()["dataset"]["id_column"])


def namespace() -> str:
    return str(load_config()["monitoring"]["namespace"])


def ensure_dirs() -> None:
    for path in (DATA_DIR, PREDICTIONS_DIR, EVIDENCE_DIR):
        path.mkdir(parents=True, exist_ok=True)
