"""Torna `final_project` importável mesmo sem o PYTHONPATH do Makefile, e
concentra os fixtures compartilhados entre os arquivos de teste do
Trabalho Final."""

from __future__ import annotations

import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from final_project.config import Config, load_config


def build_config(root: Path, **overrides) -> Config:
    """Config mínima para testes que não dependem do dataset real (ex.:
    `solution.py`, que só lê `cfg.root` e escreve em `cfg.evidence_dir`).
    Valores fora de `root`/`evidence_dir` são placeholders plausíveis."""
    evidence_dir = overrides.pop("evidence_dir", root / "artifacts" / "evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    defaults = {
        "root": root,
        "artifacts_dir": root / "artifacts",
        "data_dir": root / "artifacts" / "data",
        "evidence_dir": evidence_dir,
        "generated_dir": root / ".generated",
        "terraform_dir": root / "terraform",
        "region": "us-east-1",
        "student_id": "teste",
        "prefix": "fiap-final-teste",
        "seed": 20260913,
        "feature_order": [
            "tenure_months",
            "monthly_charges",
            "support_calls_90d",
            "payment_delay_days",
            "usage_score",
            "annual_contract",
            "premium_plan",
        ],
        "label_column": "churn",
        "id_column": "observation_id",
        "psi_threshold": 0.20,
        "namespace": "FIAP/BoraFibra/Final",
        "scenario": {},
        "acceptance": {},
    }
    defaults.update(overrides)
    return Config(**defaults)


@pytest.fixture
def minimal_config(tmp_path):
    """Config isolada em `tmp_path`, para testes de `solution.py` que não
    devem tocar em nada fora do diretório temporário."""
    return build_config(tmp_path)


@pytest.fixture(scope="session")
def real_config() -> Config:
    """Config real do projeto, lendo `config/scenario.yaml` e
    `config/acceptance.yaml` de verdade — usada por `test_data_contract.py`
    contra os CSV reais em `artifacts/data/`."""
    return load_config(root=PROJECT_ROOT)


@pytest.fixture
def copied_data_config(tmp_path, real_config):
    """Cópia de `artifacts/data/` em `tmp_path`, com uma Config apontando
    para lá. Existe para os testes de corrupção poderem estragar um CSV sem
    jamais tocar nos arquivos reais do repositório."""
    data_copy = tmp_path / "data"
    shutil.copytree(real_config.data_dir, data_copy)
    evidence_copy = tmp_path / "evidence"
    evidence_copy.mkdir(parents=True, exist_ok=True)
    return replace(real_config, data_dir=data_copy, evidence_dir=evidence_copy)
