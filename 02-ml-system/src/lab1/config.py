"""Caminhos, constantes e o helper de log compartilhados por todos os scripts.

Convenção válida para o lab inteiro: stdout carrega o *resultado* (o dado que
alguém pode pipar ou capturar), stderr carrega progresso e diagnóstico.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
TERRAFORM_DIR = REPO_ROOT / "terraform"
ARTIFACTS_DIR = Path(os.environ.get("LAB1_ARTIFACTS_DIR", REPO_ROOT / "artifacts"))
DATA_DIR = ARTIFACTS_DIR / "data"
EVIDENCE_DIR = ARTIFACTS_DIR / "evidence"

LAB_YAML = CONFIG_DIR / "lab.yaml"
SCHEMA_JSON = CONFIG_DIR / "schema.json"

# O nome do arquivo já declara o propósito: "model_" significa sem cabeçalho, na
# ordem do contrato de features, pronto para o SageMaker consumir.
SOURCE_FILE = "source.csv"
MODEL_TRAIN_FILE = "model_train_headerless.csv"
MODEL_VALIDATION_FILE = "model_validation_headerless.csv"
MODEL_TEST_FEATURES_FILE = "model_test_features_headerless.csv"
TEST_LABELS_FILE = "test_labels.csv"
MANIFEST_FILE = "dataset_manifest.json"

SPLITS = ("train", "validation", "test")


def log(*args: Any) -> None:
    """Progresso/diagnóstico. Nunca em stdout, para `cmd | tail` sair limpo."""
    print(*args, file=sys.stderr, flush=True)


def emit(payload: Any) -> None:
    """O único resultado que alguém pode capturar, como JSON em stdout."""
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


@dataclass(frozen=True)
class LabConfig:
    raw: dict[str, Any]

    @property
    def schema_version(self) -> str:
        return str(self.raw["schema_version"])

    @property
    def region(self) -> str:
        return str(self.raw["region"])

    @property
    def execution_role_name(self) -> str:
        return str(self.raw["aws"]["execution_role_name"])

    @property
    def bucket_prefix(self) -> str:
        return str(self.raw["aws"]["bucket_prefix"])

    def bucket_name(self, account_id: str) -> str:
        return f"{self.bucket_prefix}-{account_id}"

    @property
    def seed(self) -> int:
        return int(self.raw["dataset"]["seed"])

    @property
    def rows(self) -> int:
        return int(self.raw["dataset"]["rows"])

    @property
    def label(self) -> str:
        return str(self.raw["dataset"]["label"])

    @property
    def id_column(self) -> str:
        return str(self.raw["dataset"]["id_column"])

    @property
    def feature_order(self) -> list[str]:
        return list(self.raw["dataset"]["feature_order"])

    @property
    def split_fractions(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.raw["dataset"]["split"].items()}

    @property
    def bounds(self) -> dict[str, tuple[float, float]]:
        return {k: (float(v[0]), float(v[1])) for k, v in self.raw["bounds"].items()}

    @property
    def prevalence_range(self) -> tuple[float, float]:
        lo, hi = self.raw["prevalence_range"]
        return float(lo), float(hi)

    @property
    def min_rows_per_split(self) -> int:
        return int(self.raw["min_rows_per_split"])

    @property
    def acceptance(self) -> dict[str, Any]:
        return dict(self.raw["acceptance"])

    @property
    def decision_threshold(self) -> float:
        return float(self.raw["acceptance"]["decision_threshold"])


def load_config(path: Path | None = None) -> LabConfig:
    with open(path or LAB_YAML, encoding="utf-8") as handle:
        return LabConfig(yaml.safe_load(handle))


def load_schema(path: Path | None = None) -> dict[str, Any]:
    with open(path or SCHEMA_JSON, encoding="utf-8") as handle:
        return json.load(handle)


def data_dir(base: Path | None = None) -> Path:
    return (base / "data") if base is not None else DATA_DIR


def evidence_dir(base: Path | None = None) -> Path:
    return (base / "evidence") if base is not None else EVIDENCE_DIR
