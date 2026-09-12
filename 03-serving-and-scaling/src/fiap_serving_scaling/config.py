"""Caminhos, constantes e o helper de log usados por todo comando de scripts/lab.py.

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
ARTIFACTS_DIR = Path(os.environ.get("LAB2_ARTIFACTS_DIR", REPO_ROOT / "artifacts"))
DATA_DIR = ARTIFACTS_DIR / "data"
EVIDENCE_DIR = ARTIFACTS_DIR / "evidence"

LAB_YAML = CONFIG_DIR / "lab.yaml"
SCHEMA_JSON = CONFIG_DIR / "schema.json"

TRAIN_FILE = "train.csv"
VALIDATION_FILE = "validation.csv"
TEST_LABELED_FILE = "test_labeled.csv"
TEST_FEATURES_FILE = "test_features.csv"
ASYNC_PAYLOAD_FILE = "async_payload.csv"
BATCH_INPUT_FILE = "batch_input.csv"
MANIFEST_FILE = "dataset_manifest.json"


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
    def region(self) -> str:
        return str(self.raw["region"])

    @property
    def execution_role_name(self) -> str:
        return str(self.raw["aws"]["execution_role_name"])

    @property
    def bucket_prefix(self) -> str:
        return str(self.raw["aws"]["bucket_prefix"])

    def bucket_name(self, account_id: str) -> str:
        return f"{self.bucket_prefix}-{account_id}-{self.region}"

    @property
    def seed(self) -> int:
        return int(self.raw["dataset"]["seed"])

    @property
    def n_samples(self) -> int:
        return int(self.raw["dataset"]["n_samples"])

    @property
    def n_features(self) -> int:
        return int(self.raw["dataset"]["n_features"])

    @property
    def n_informative(self) -> int:
        return int(self.raw["dataset"]["n_informative"])

    @property
    def n_redundant(self) -> int:
        return int(self.raw["dataset"]["n_redundant"])

    @property
    def label(self) -> str:
        return str(self.raw["dataset"]["label"])

    @property
    def feature_names(self) -> list[str]:
        return [f"{self.raw['dataset']['feature_prefix']}{i}" for i in range(self.n_features)]

    @property
    def split_rows(self) -> dict[str, int]:
        return {k: int(v) for k, v in self.raw["dataset"]["split"].items()}

    @property
    def async_payload_rows(self) -> int:
        return int(self.raw["dataset"]["async_payload_rows"])

    @property
    def predictions_tolerance(self) -> float:
        return float(self.raw["acceptance"]["predictions_tolerance"])

    @property
    def load_test_matrix(self) -> list[dict[str, int]]:
        return list(self.raw["load_test"]["matrix"])

    @property
    def load_test_min_success_rate(self) -> float:
        return float(self.raw["load_test"]["min_success_rate"])

    @property
    def scale_demo_target_min_capacity(self) -> int:
        return int(self.raw["scale_demo"]["target_min_capacity"])

    @property
    def scale_demo_wait_timeout_s(self) -> int:
        return int(self.raw["scale_demo"]["wait_timeout_s"])

    @property
    def batch(self) -> dict[str, Any]:
        return dict(self.raw["batch"])


def load_config(path: Path | None = None) -> LabConfig:
    with open(path or LAB_YAML, encoding="utf-8") as handle:
        return LabConfig(yaml.safe_load(handle))


def load_schema(path: Path | None = None) -> dict[str, Any]:
    with open(path or SCHEMA_JSON, encoding="utf-8") as handle:
        return json.load(handle)
