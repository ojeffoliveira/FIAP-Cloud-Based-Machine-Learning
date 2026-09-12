"""Geração determinística do dataset e o contrato de dados executável.

O Lab 03 usa de propósito um classificador sintético genérico
(sklearn.datasets.make_classification) em vez do gerador de features de domínio do
02-ml-system: o foco pedagógico aqui é o padrão de serving, não engenharia de
features, e isso mantém o lab tecnicamente autossuficiente (não é necessário
nenhum estado remanescente do Lab 02).
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.datasets import make_classification
from sklearn.model_selection import train_test_split

from fiap_serving_scaling.config import (
    ASYNC_PAYLOAD_FILE,
    BATCH_INPUT_FILE,
    MANIFEST_FILE,
    TEST_FEATURES_FILE,
    TEST_LABELED_FILE,
    TRAIN_FILE,
    VALIDATION_FILE,
    LabConfig,
    log,
)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_headerless_csv(path: Path, rows: np.ndarray) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow([f"{v:.6f}" if isinstance(v, float) else v for v in row])


def generate(cfg: LabConfig, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    features, labels = make_classification(
        n_samples=cfg.n_samples,
        n_features=cfg.n_features,
        n_informative=cfg.n_informative,
        n_redundant=cfg.n_redundant,
        n_classes=2,
        weights=[0.66, 0.34],
        class_sep=1.6,
        flip_y=0.02,
        random_state=cfg.seed,
    )

    split = cfg.split_rows
    n_train, n_val, n_test = split["train"], split["validation"], split["test"]

    x_train, x_rest, y_train, y_rest = train_test_split(
        features,
        labels,
        train_size=n_train,
        random_state=cfg.seed,
        stratify=labels,
    )
    x_val, x_test, y_val, y_test = train_test_split(
        x_rest,
        y_rest,
        train_size=n_val,
        test_size=n_test,
        random_state=cfg.seed,
        stratify=y_rest,
    )

    def with_label_first(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return np.column_stack([y.astype(int), np.round(x, 6)])

    log(f"[data] semente={cfg.seed} n_samples={cfg.n_samples} destino={out_dir}")

    _write_headerless_csv(out_dir / TRAIN_FILE, with_label_first(x_train, y_train))
    _write_headerless_csv(out_dir / VALIDATION_FILE, with_label_first(x_val, y_val))

    feature_names = cfg.feature_names

    with open(out_dir / TEST_LABELED_FILE, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", *feature_names, "label"])
        for idx, (row, label) in enumerate(zip(x_test, y_test, strict=True), start=1):
            writer.writerow([idx, *[f"{v:.6f}" for v in np.round(row, 6)], int(label)])

    _write_headerless_csv(out_dir / TEST_FEATURES_FILE, np.round(x_test, 6))

    async_rows = cfg.async_payload_rows
    _write_headerless_csv(out_dir / ASYNC_PAYLOAD_FILE, np.round(x_test[:async_rows], 6))
    _write_headerless_csv(out_dir / BATCH_INPUT_FILE, np.round(x_test, 6))

    prevalence = {
        "train": float(y_train.mean()),
        "validation": float(y_val.mean()),
        "test": float(y_test.mean()),
    }
    for name, count, prev in (
        ("train", n_train, prevalence["train"]),
        ("validation", n_val, prevalence["validation"]),
        ("test", n_test, prevalence["test"]),
    ):
        log(f"[data] {name:<10} {count:>4} linhas  prevalência {prev:.4f}")

    manifest = {
        "schema_version": "1.0.0",
        "seed": cfg.seed,
        "n_samples": cfg.n_samples,
        "feature_order": feature_names,
        "label": cfg.label,
        "rows": {"train": n_train, "validation": n_val, "test": n_test},
        "prevalence": prevalence,
        "sha256": {},
    }
    for filename in (
        TRAIN_FILE,
        VALIDATION_FILE,
        TEST_LABELED_FILE,
        TEST_FEATURES_FILE,
        ASYNC_PAYLOAD_FILE,
        BATCH_INPUT_FILE,
    ):
        manifest["sha256"][filename] = sha256_of(out_dir / filename)

    with open(out_dir / MANIFEST_FILE, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)

    log(f"[data] escrito em {out_dir}")
    return manifest


# --------------------------------------------------------------------------- #
# Contrato executável
# --------------------------------------------------------------------------- #


def _read_headerless_floats(path: Path) -> list[list[float]]:
    with open(path, encoding="utf-8") as handle:
        return [[float(v) for v in line.strip().split(",")] for line in handle if line.strip()]


def validate(cfg: LabConfig, data_dir: Path) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}

    files = [
        TRAIN_FILE,
        VALIDATION_FILE,
        TEST_LABELED_FILE,
        TEST_FEATURES_FILE,
        ASYNC_PAYLOAD_FILE,
        BATCH_INPUT_FILE,
        MANIFEST_FILE,
    ]
    present = {name: (data_dir / name).exists() for name in files}
    checks["files.present"] = all(present.values())
    details["files_present"] = present

    if not checks["files.present"]:
        return {"passed": False, "checks": checks, "details": details}

    with open(data_dir / MANIFEST_FILE, encoding="utf-8") as handle:
        manifest = json.load(handle)

    split = cfg.split_rows
    train_rows = _read_headerless_floats(data_dir / TRAIN_FILE)
    val_rows = _read_headerless_floats(data_dir / VALIDATION_FILE)
    test_features_rows = _read_headerless_floats(data_dir / TEST_FEATURES_FILE)
    batch_rows = _read_headerless_floats(data_dir / BATCH_INPUT_FILE)

    checks["row_count.train"] = len(train_rows) == split["train"]
    checks["row_count.validation"] = len(val_rows) == split["validation"]
    checks["row_count.test_features"] = len(test_features_rows) == split["test"]
    checks["row_count.batch_input"] = len(batch_rows) == split["test"]

    n_features = cfg.n_features
    checks["column_count.train"] = all(len(r) == n_features + 1 for r in train_rows)
    checks["column_count.validation"] = all(len(r) == n_features + 1 for r in val_rows)
    checks["column_count.test_features"] = all(len(r) == n_features for r in test_features_rows)

    labels_train = [r[0] for r in train_rows]
    labels_val = [r[0] for r in val_rows]
    checks["label.binary"] = set(labels_train) <= {0.0, 1.0} and set(labels_val) <= {0.0, 1.0}

    def finite(rows: list[list[float]]) -> bool:
        return all(v == v and abs(v) != float("inf") for row in rows for v in row)

    checks["values.no_nan_or_inf"] = finite(train_rows) and finite(val_rows) and finite(test_features_rows)

    # Prova que não há vazamento de rótulo nem desvio na ordem das colunas: as
    # features do test_labeled.csv (com colunas id/label explícitas) precisam ser
    # iguais, valor por valor, à linha correspondente do test_features.csv sem
    # cabeçalho usado na inferência. Uma reordenação silenciosa ou uma coluna de
    # rótulo a mais por acidente apareceria aqui como divergência, não como um
    # número errado de aparência plausível.
    with open(data_dir / TEST_LABELED_FILE, encoding="utf-8") as handle:
        labeled_reader = csv.DictReader(handle)
        labeled_features = [
            [float(row[name]) for name in cfg.feature_names] for row in labeled_reader
        ]
    checks["test_features.matches_labeled_features_no_leak"] = labeled_features == test_features_rows
    checks["feature_order.matches_manifest"] = manifest["feature_order"] == cfg.feature_names

    sha_ok = True
    recomputed: dict[str, str] = {}
    for filename, expected in manifest["sha256"].items():
        actual = sha256_of(data_dir / filename)
        recomputed[filename] = actual
        if actual != expected:
            sha_ok = False
    checks["manifest.sha256_matches_files"] = sha_ok
    details["sha256"] = recomputed
    details["rows"] = {
        "train": len(train_rows),
        "validation": len(val_rows),
        "test_features": len(test_features_rows),
        "batch_input": len(batch_rows),
    }

    passed = all(checks.values())
    return {"passed": passed, "checks": checks, "details": details}
