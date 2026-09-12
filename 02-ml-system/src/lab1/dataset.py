"""Dataset sintético determinístico do Lab 02.

Por que sintético: a aula precisa que toda conta do Academy rode o *mesmo
experimento lógico*. Um dataset baixado pode mudar ou sair do ar de forma
independente deste repositório; um gerador com semente fixa não pode.

Contrato de determinismo
------------------------
Duas propriedades são garantidas, nesta ordem:

1. Mesma semente + mesma versão do numpy -> bytes idênticos em todo arquivo
   gerado.
2. Estabilidade entre plataformas (arm64 vs x86_64): toda feature é arredondada
   para um número fixo de decimais *antes* de o rótulo ser calculado e antes de
   ser escrita. Funções transcendentais (exp/log dentro de normal/gamma/poisson)
   podem diferir em um ULP entre implementações SIMD; arredondar para 2 decimais
   absorve isso, então os bytes do CSV continuam idênticos. O sorteio do rótulo
   usa `rng.random() < p` em vez de `rng.binomial`, porque `random()` é pura
   manipulação de bits e, portanto, exato em qualquer lugar.

A ordem dos sorteios abaixo faz parte do contrato: reordenar muda o fluxo do RNG
e, com isso, o hash de todos os arquivos.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from lab1.config import (
    MANIFEST_FILE,
    MODEL_TEST_FEATURES_FILE,
    MODEL_TRAIN_FILE,
    MODEL_VALIDATION_FILE,
    SOURCE_FILE,
    TEST_LABELS_FILE,
    LabConfig,
    log,
)

# Coeficientes do processo gerador dos dados. São um modelo logístico
# transparente: quem lê consegue prever a direção do efeito de cada feature sem
# treinar nada, e é justamente esse o ponto na primeira aula.
DGP = {
    "intercept": -0.45,
    "tenure_months": -0.045,
    "support_calls_90d": 0.65,
    "payment_delay_days": 0.065,
    "monthly_charges": 0.012,
    "monthly_charges_center": 100.0,
    "annual_contract": -0.95,
    "premium_plan": -0.40,
    "usage_score": -0.018,
    "usage_score_center": 60.0,
}

DECIMALS = {
    "tenure_months": 0,
    "monthly_charges": 2,
    "support_calls_90d": 0,
    "payment_delay_days": 2,
    "usage_score": 2,
    "annual_contract": 0,
    "premium_plan": 0,
    "churn": 0,
}


@dataclass(frozen=True)
class Split:
    name: str
    ids: np.ndarray
    features: dict[str, np.ndarray]
    labels: np.ndarray

    @property
    def rows(self) -> int:
        return int(self.ids.shape[0])

    @property
    def prevalence(self) -> float:
        return float(self.labels.mean())


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def generate_source(cfg: LabConfig) -> dict[str, np.ndarray]:
    """Gera a representação de análise, incluindo o `observation_id`."""
    rng = np.random.default_rng(cfg.seed)
    n = cfg.rows

    observation_id = np.arange(1, n + 1, dtype=np.int64)

    # A ordem dos sorteios faz parte do contrato de determinismo - não reordenar.
    tenure_months = rng.integers(1, 73, size=n).astype(np.int64)
    monthly_charges = np.round(np.clip(rng.normal(110.0, 45.0, size=n), 20.0, 260.0), 2)
    support_calls_90d = np.clip(rng.poisson(1.5, size=n), 0, 30).astype(np.int64)
    payment_delay_days = np.round(np.clip(rng.gamma(2.0, 5.0, size=n), 0.0, 45.0), 2)
    usage_score = np.round(np.clip(rng.normal(65.0, 20.0, size=n), 5.0, 100.0), 2)
    annual_contract = (rng.random(size=n) < 0.45).astype(np.int64)
    premium_plan = (rng.random(size=n) < 0.35).astype(np.int64)

    logit = (
        DGP["intercept"]
        + DGP["tenure_months"] * tenure_months
        + DGP["support_calls_90d"] * support_calls_90d
        + DGP["payment_delay_days"] * payment_delay_days
        + DGP["monthly_charges"] * (monthly_charges - DGP["monthly_charges_center"])
        + DGP["annual_contract"] * annual_contract
        + DGP["premium_plan"] * premium_plan
        + DGP["usage_score"] * (usage_score - DGP["usage_score_center"])
    )
    churn = (rng.random(size=n) < _sigmoid(logit)).astype(np.int64)

    return {
        "observation_id": observation_id,
        "tenure_months": tenure_months,
        "monthly_charges": monthly_charges,
        "support_calls_90d": support_calls_90d,
        "payment_delay_days": payment_delay_days,
        "usage_score": usage_score,
        "annual_contract": annual_contract,
        "premium_plan": premium_plan,
        "churn": churn,
    }


def stratified_split(cfg: LabConfig, source: dict[str, np.ndarray]) -> dict[str, Split]:
    """Split estratificado determinístico, disjunto por construção.

    Cada classe é permutada de forma independente com o próprio gerador semeado e
    depois cortada nas frações configuradas. No fim as linhas são ordenadas por
    `observation_id`, para o layout do arquivo não depender da ordem de
    concatenação.
    """
    labels = source["churn"]
    fractions = cfg.split_fractions
    train_cut, validation_cut = fractions["train"], fractions["train"] + fractions["validation"]

    # Semente deslocada para o fluxo do split nunca coincidir com o do gerador.
    rng = np.random.default_rng(cfg.seed + 1)

    buckets: dict[str, list[np.ndarray]] = {"train": [], "validation": [], "test": []}
    for class_value in (0, 1):
        class_positions = np.flatnonzero(labels == class_value)
        shuffled = rng.permutation(class_positions)
        n_class = shuffled.shape[0]
        i_train = int(round(n_class * train_cut))
        i_validation = int(round(n_class * validation_cut))
        buckets["train"].append(shuffled[:i_train])
        buckets["validation"].append(shuffled[i_train:i_validation])
        buckets["test"].append(shuffled[i_validation:])

    splits: dict[str, Split] = {}
    for name, parts in buckets.items():
        positions = np.sort(np.concatenate(parts))
        splits[name] = Split(
            name=name,
            ids=source["observation_id"][positions],
            features={f: source[f][positions] for f in cfg.feature_order},
            labels=labels[positions],
        )
    return splits


def _format(column: str, value: Any) -> str:
    decimals = DECIMALS[column]
    if decimals == 0:
        return str(int(value))
    return f"{float(value):.{decimals}f}"


def _write_csv(path: Path, header: Sequence[str] | None, rows: Iterable[Sequence[str]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        if header is not None:
            writer.writerow(header)
        writer.writerows(rows)
    return sha256_file(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def serialize_training_rows(cfg: LabConfig, split: Split) -> list[list[str]]:
    """Sem cabeçalho, rótulo na primeira coluna - o contrato de treino do XGBoost."""
    columns = [cfg.label, *cfg.feature_order]
    values = {cfg.label: split.labels, **split.features}
    return [[_format(c, values[c][i]) for c in columns] for i in range(split.rows)]


def serialize_feature_rows(cfg: LabConfig, split: Split) -> list[list[str]]:
    """Sem cabeçalho, sem rótulo - o contrato de inferência."""
    return [
        [_format(c, split.features[c][i]) for c in cfg.feature_order]
        for i in range(split.rows)
    ]


def write_dataset(cfg: LabConfig, out_dir: Path) -> dict[str, Any]:
    """Escreve o source + os arquivos prontos para o modelo e devolve o manifesto."""
    source = generate_source(cfg)
    splits = stratified_split(cfg, source)

    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"[data] semente={cfg.seed} linhas={cfg.rows} destino={out_dir}")

    source_columns = [cfg.id_column, *cfg.feature_order, cfg.label]
    source_rows = [
        [str(int(source[cfg.id_column][i]))]
        + [_format(c, source[c][i]) for c in cfg.feature_order]
        + [_format(cfg.label, source[cfg.label][i])]
        for i in range(cfg.rows)
    ]
    source_sha = _write_csv(out_dir / SOURCE_FILE, source_columns, source_rows)

    train_sha = _write_csv(
        out_dir / MODEL_TRAIN_FILE, None, serialize_training_rows(cfg, splits["train"])
    )
    validation_sha = _write_csv(
        out_dir / MODEL_VALIDATION_FILE, None, serialize_training_rows(cfg, splits["validation"])
    )
    test_features_sha = _write_csv(
        out_dir / MODEL_TEST_FEATURES_FILE, None, serialize_feature_rows(cfg, splits["test"])
    )
    test_labels_sha = _write_csv(
        out_dir / TEST_LABELS_FILE,
        [cfg.id_column, cfg.label],
        [
            [str(int(splits["test"].ids[i])), _format(cfg.label, splits["test"].labels[i])]
            for i in range(splits["test"].rows)
        ],
    )

    manifest: dict[str, Any] = {
        "schema_version": cfg.schema_version,
        "seed": cfg.seed,
        "rows": cfg.rows,
        "label": cfg.label,
        "id_column": cfg.id_column,
        "feature_order": cfg.feature_order,
        "generator": {
            "kind": "logistic-dgp",
            "numpy_version": np.__version__,
            "coefficients": DGP,
            "decimals": DECIMALS,
        },
        "source": {
            "file": SOURCE_FILE,
            "rows": cfg.rows,
            "sha256": source_sha,
            "prevalence": round(float(source[cfg.label].mean()), 6),
        },
        "splits": {
            "train": {
                "file": MODEL_TRAIN_FILE,
                "rows": splits["train"].rows,
                "sha256": train_sha,
                "prevalence": round(splits["train"].prevalence, 6),
                "header": False,
                "label_position": "first",
            },
            "validation": {
                "file": MODEL_VALIDATION_FILE,
                "rows": splits["validation"].rows,
                "sha256": validation_sha,
                "prevalence": round(splits["validation"].prevalence, 6),
                "header": False,
                "label_position": "first",
            },
            "test": {
                "file": MODEL_TEST_FEATURES_FILE,
                "rows": splits["test"].rows,
                "sha256": test_features_sha,
                "labels_file": TEST_LABELS_FILE,
                "labels_sha256": test_labels_sha,
                "prevalence": round(splits["test"].prevalence, 6),
                "header": False,
                "label_present_in_features": False,
            },
        },
    }

    manifest_path = out_dir / MANIFEST_FILE
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest["manifest_sha256"] = sha256_file(manifest_path)

    return manifest


def read_manifest(out_dir: Path) -> dict[str, Any]:
    with (out_dir / MANIFEST_FILE).open(encoding="utf-8") as handle:
        return json.load(handle)
