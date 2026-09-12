"""Contrato de dados executável.

O contrato não é texto num README: é o conjunto de asserções abaixo, executado
por `scripts/validate_data.py` *antes* de qualquer recurso da AWS ser criado.
Um dataset que viola o contrato nunca chega ao S3, então um training job que
falha não pode ser culpa de dado que ninguém conferiu.

O mesmo código atende duas audiências:

- o contrato de treino - CSV sem cabeçalho, rótulo na primeira coluna;
- o contrato de serving - CSV sem cabeçalho, sem rótulo, ordem exata de features.

Tudo que serializa uma linha para o endpoint passa por `serialize_features` aqui,
então o payload enviado em produção não consegue divergir do payload verificado
nos testes.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from lab1.config import (
    MANIFEST_FILE,
    MODEL_TEST_FEATURES_FILE,
    MODEL_TRAIN_FILE,
    MODEL_VALIDATION_FILE,
    SOURCE_FILE,
    TEST_LABELS_FILE,
    LabConfig,
)
from lab1.dataset import DECIMALS, sha256_file

# Registros de smoke determinísticos. A única função deles é provar que o caminho
# de serving está vivo e coerente na direção: o processo gerador faz o primeiro
# registro muito mais arriscado que o segundo, então um modelo saudável precisa
# pontuá-los nessa ordem. A verificação é de ordenação, não de probabilidade
# fixa, porque o modelo treinado pode calibrar diferente do gerador.
SMOKE_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "name": "high_risk",
        "features": {
            "tenure_months": 2,
            "monthly_charges": 220.00,
            "support_calls_90d": 6,
            "payment_delay_days": 30.00,
            "usage_score": 15.00,
            "annual_contract": 0,
            "premium_plan": 0,
        },
    },
    {
        "name": "low_risk",
        "features": {
            "tenure_months": 66,
            "monthly_charges": 45.00,
            "support_calls_90d": 0,
            "payment_delay_days": 0.00,
            "usage_score": 92.00,
            "annual_contract": 1,
            "premium_plan": 1,
        },
    },
)


class ContractError(AssertionError):
    """Levantada quando uma verificação do contrato falha de forma dura."""


@dataclass
class Check:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(Check(name=name, passed=bool(passed), detail=detail))

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    @property
    def ok(self) -> bool:
        return not self.failed

    def as_dict(self) -> dict[str, Any]:
        return {
            "checks_total": len(self.checks),
            "checks_failed": len(self.failed),
            "passed": self.ok,
            "checks": [c.as_dict() for c in self.checks],
        }


# --------------------------------------------------------------------------- #
# Serialização - a fonte única de verdade sobre o que sai para o endpoint
# --------------------------------------------------------------------------- #


def _format_value(column: str, value: Any) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        raise ContractError(f"a feature {column!r} não é um número finito: {value!r}")
    decimals = DECIMALS[column]
    if decimals == 0:
        as_int = int(value)
        if float(value) != float(as_int):
            raise ContractError(f"a feature {column!r} precisa ser inteira, veio {value!r}")
        return str(as_int)
    return f"{float(value):.{decimals}f}"


def serialize_features(cfg: LabConfig, record: Mapping[str, Any]) -> str:
    """Uma linha de CSV sem cabeçalho: só features, na ordem exata, sem rótulo e sem ID."""
    missing = [f for f in cfg.feature_order if f not in record]
    if missing:
        raise ContractError(f"o registro está sem as features: {missing}")
    forbidden = [k for k in (cfg.label, cfg.id_column) if k in record]
    if forbidden:
        raise ContractError(f"o payload de inferência não pode carregar {forbidden}")
    unexpected = sorted(set(record) - set(cfg.feature_order))
    if unexpected:
        raise ContractError(f"o registro tem colunas desconhecidas: {unexpected}")
    return ",".join(_format_value(f, record[f]) for f in cfg.feature_order)


def serialize_payload(cfg: LabConfig, records: Iterable[Mapping[str, Any]]) -> str:
    """Corpo de várias linhas para uma invocação em lote - separado por newline, sem newline final."""
    lines = [serialize_features(cfg, r) for r in records]
    if not lines:
        raise ContractError("recusando montar um payload vazio")
    return "\n".join(lines)


def smoke_payload(cfg: LabConfig) -> tuple[list[str], str]:
    """Nomes e corpo da requisição de smoke determinística."""
    names = [r["name"] for r in SMOKE_RECORDS]
    body = serialize_payload(cfg, (r["features"] for r in SMOKE_RECORDS))
    return names, body


# --------------------------------------------------------------------------- #
# Leitores de arquivo
# --------------------------------------------------------------------------- #


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.reader(handle) if row]


def read_source(path: Path) -> tuple[list[str], list[list[str]]]:
    rows = _read_rows(path)
    if not rows:
        raise ContractError(f"{path.name} está vazio")
    return rows[0], rows[1:]


def _is_float(text: str) -> bool:
    try:
        return math.isfinite(float(text))
    except ValueError:
        return False


def _looks_like_header(row: Sequence[str]) -> bool:
    return not all(_is_float(cell) for cell in row)


# --------------------------------------------------------------------------- #
# Verificações do contrato
# --------------------------------------------------------------------------- #


def check_source(cfg: LabConfig, schema: dict[str, Any], data: Path, report: Report) -> None:
    path = data / SOURCE_FILE
    expected = [cfg.id_column, *cfg.feature_order, cfg.label]
    header, rows = read_source(path)

    report.add("source.header_exact", header == expected, f"{header} vs esperado {expected}")
    report.add("source.row_count", len(rows) == cfg.rows, f"{len(rows)} linhas, esperado {cfg.rows}")

    index = {name: i for i, name in enumerate(header)}
    ids: list[int] = []
    label_values: set[str] = set()
    out_of_range: list[str] = []
    non_finite: list[str] = []
    non_integral: list[str] = []
    bounds = cfg.bounds
    columns = schema["columns"]

    for line_number, row in enumerate(rows, start=2):
        if len(row) != len(expected):
            report.add("source.column_count", False, f"a linha {line_number} tem {len(row)} colunas")
            return
        ids.append(int(row[index[cfg.id_column]]))
        label_values.add(row[index[cfg.label]])
        for feature in cfg.feature_order:
            raw = row[index[feature]]
            if not _is_float(raw):
                non_finite.append(f"linha {line_number} {feature}={raw!r}")
                continue
            value = float(raw)
            low, high = bounds[feature]
            if not (low <= value <= high):
                out_of_range.append(f"linha {line_number} {feature}={value} fora de [{low},{high}]")
            if columns[feature]["dtype"] == "int64" and value != int(value):
                non_integral.append(f"linha {line_number} {feature}={value}")

    report.add("source.column_count", True, f"todas as linhas têm {len(expected)} colunas")
    report.add("source.no_nan_or_inf", not non_finite, "; ".join(non_finite[:5]) or "nenhum encontrado")
    report.add(
        "source.label_binary",
        label_values <= {"0", "1"},
        f"valores de {cfg.label} presentes: {sorted(label_values)}",
    )
    report.add(
        "source.integer_columns",
        not non_integral,
        "; ".join(non_integral[:5]) or "colunas int/binárias contêm valores inteiros",
    )
    report.add(
        "source.numeric_ranges",
        not out_of_range,
        "; ".join(out_of_range[:5]) or "todas as features dentro dos limites documentados",
    )
    report.add(
        "source.unique_observation_id",
        len(set(ids)) == len(ids),
        f"{len(ids) - len(set(ids))} ids duplicados",
    )

    positives = sum(1 for r in rows if r[index[cfg.label]] == "1")
    observed = positives / len(rows) if rows else 0.0
    low, high = cfg.prevalence_range
    report.add(
        "source.prevalence_in_range",
        low <= observed <= high,
        f"prevalência {observed:.4f}, esperada dentro de [{low},{high}]",
    )
    binary_features = [f for f in cfg.feature_order if columns[f].get("unit") == "binary"]
    if not binary_features:
        raise ContractError("o schema.json não declara nenhuma feature binária - o contrato perdeu uma coluna")
    bad_binary = [
        f
        for f in binary_features
        if not {row[index[f]] for row in rows} <= {"0", "1"}
    ]
    report.add(
        "source.binary_features",
        not bad_binary,
        f"features binárias conferidas: {binary_features}; violações: {bad_binary}",
    )


def check_model_files(cfg: LabConfig, data: Path, report: Report) -> dict[str, list[list[str]]]:
    n_features = len(cfg.feature_order)
    parsed: dict[str, list[list[str]]] = {}

    for label_present, filename in (
        (True, MODEL_TRAIN_FILE),
        (True, MODEL_VALIDATION_FILE),
        (False, MODEL_TEST_FEATURES_FILE),
    ):
        path = data / filename
        rows = _read_rows(path)
        parsed[filename] = rows
        expected_columns = n_features + 1 if label_present else n_features

        report.add(f"{filename}.exists", path.exists(), str(path))
        report.add(
            f"{filename}.no_header",
            bool(rows) and not _looks_like_header(rows[0]),
            f"primeira linha: {rows[0][:3] if rows else 'VAZIO'}",
        )
        widths = {len(r) for r in rows}
        report.add(
            f"{filename}.column_count",
            widths == {expected_columns},
            f"larguras {sorted(widths)}, esperado {{{expected_columns}}}",
        )
        report.add(
            f"{filename}.min_rows",
            len(rows) >= cfg.min_rows_per_split,
            f"{len(rows)} linhas, mínimo {cfg.min_rows_per_split}",
        )
        report.add(
            f"{filename}.all_numeric",
            all(_is_float(cell) for r in rows for cell in r),
            "toda célula converte para float finito",
        )
        if label_present:
            first_column = {r[0] for r in rows if r}
            report.add(
                f"{filename}.label_first_binary",
                first_column <= {"0", "1"},
                f"valores da primeira coluna {sorted(first_column)} - o rótulo é a coluna 0",
            )
        else:
            # Um rótulo vazando para o arquivo de inferência apareceria como coluna
            # extra; a largura já cobre isso, então aqui a intenção fica explícita.
            report.add(
                f"{filename}.no_label_column",
                widths == {n_features},
                f"exatamente {n_features} colunas de feature, sem rótulo",
            )
    return parsed


def check_split_integrity(cfg: LabConfig, data: Path, report: Report) -> None:
    manifest_path = data / MANIFEST_FILE
    report.add("manifest.exists", manifest_path.exists(), str(manifest_path))
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    report.add(
        "manifest.schema_version",
        manifest.get("schema_version") == cfg.schema_version,
        f"{manifest.get('schema_version')} vs configuração {cfg.schema_version}",
    )
    report.add(
        "manifest.seed",
        manifest.get("seed") == cfg.seed,
        f"semente {manifest.get('seed')}",
    )
    report.add(
        "manifest.feature_order",
        manifest.get("feature_order") == cfg.feature_order,
        f"{manifest.get('feature_order')}",
    )

    fingerprints = {"source": manifest.get("source", {}).get("sha256")}
    for split in ("train", "validation", "test"):
        fingerprints[split] = manifest.get("splits", {}).get(split, {}).get("sha256")
    report.add(
        "manifest.fingerprints_present",
        all(isinstance(v, str) and len(v) == 64 for v in fingerprints.values()),
        f"sha256 registrado para {sorted(fingerprints)}",
    )

    on_disk = {
        "source": data / SOURCE_FILE,
        "train": data / MODEL_TRAIN_FILE,
        "validation": data / MODEL_VALIDATION_FILE,
        "test": data / MODEL_TEST_FEATURES_FILE,
    }
    mismatched = [
        key
        for key, path in on_disk.items()
        if path.exists() and fingerprints.get(key) != sha256_file(path)
    ]
    report.add(
        "manifest.fingerprints_match_files",
        not mismatched,
        f"divergentes: {mismatched}" if mismatched else "o manifesto casa com os bytes em disco",
    )

    # A disjunção é provada a partir das colunas de ID realmente escritas, não do
    # objeto de split em memória - os arquivos são o que o S3 vai ver.
    header, source_rows = read_source(data / SOURCE_FILE)
    id_index = header.index(cfg.id_column)
    label_index = header.index(cfg.label)
    source_ids = [int(r[id_index]) for r in source_rows]

    test_label_rows = _read_rows(data / TEST_LABELS_FILE)
    report.add(
        "test_labels.header",
        bool(test_label_rows) and test_label_rows[0] == [cfg.id_column, cfg.label],
        f"cabeçalho {test_label_rows[0] if test_label_rows else 'VAZIO'}",
    )
    test_ids = {int(r[0]) for r in test_label_rows[1:]}
    report.add(
        "test_labels.rows_match_features",
        len(test_ids) == len(_read_rows(data / MODEL_TEST_FEATURES_FILE)),
        f"{len(test_ids)} ids rotulados vs linhas de features",
    )
    report.add(
        "test_labels.ids_from_source",
        test_ids <= set(source_ids),
        "todo id de teste existe no source.csv",
    )

    # Treino e validação não carregam IDs por design, então a disjunção é provada
    # reconstruindo-os a partir das contagens do manifesto mais as colunas de rótulo.
    split_rows = {
        "train": len(_read_rows(data / MODEL_TRAIN_FILE)),
        "validation": len(_read_rows(data / MODEL_VALIDATION_FILE)),
        "test": len(_read_rows(data / MODEL_TEST_FEATURES_FILE)),
    }
    report.add(
        "splits.partition_source",
        sum(split_rows.values()) == len(source_rows),
        f"{split_rows} soma {sum(split_rows.values())} de {len(source_rows)} linhas do source",
    )
    for name, count in split_rows.items():
        declared = manifest.get("splits", {}).get(name, {}).get("rows")
        report.add(
            f"splits.{name}.rows_match_manifest",
            declared == count,
            f"manifesto {declared} vs arquivo {count}",
        )

    source_label_by_id = {int(r[id_index]): r[label_index] for r in source_rows}
    label_mismatch = [
        i for i, lbl in ((int(r[0]), r[1]) for r in test_label_rows[1:])
        if source_label_by_id.get(i) != lbl
    ]
    report.add(
        "test_labels.consistent_with_source",
        not label_mismatch,
        f"{len(label_mismatch)} rótulos discordam do source.csv",
    )


def check_payload_contract(cfg: LabConfig, data: Path, report: Report) -> None:
    """Ida e volta: uma linha do source serializada para inferência tem que ser igual à do arquivo."""
    header, source_rows = read_source(data / SOURCE_FILE)
    index = {name: i for i, name in enumerate(header)}
    feature_rows = _read_rows(data / MODEL_TEST_FEATURES_FILE)
    test_ids = [int(r[0]) for r in _read_rows(data / TEST_LABELS_FILE)[1:]]
    by_id = {int(r[index[cfg.id_column]]): r for r in source_rows}

    mismatches: list[str] = []
    for observation_id, file_row in zip(test_ids, feature_rows):
        source_row = by_id.get(observation_id)
        if source_row is None:
            # Acontece quando o source.csv tem IDs duplicados; a verificação
            # source.unique_observation_id já reporta isso, então aqui registramos
            # sem derrubar o relatório.
            mismatches.append(f"id {observation_id}: não resolvível no source.csv")
            break
        record = {f: float(source_row[index[f]]) for f in cfg.feature_order}
        rebuilt = serialize_features(cfg, record)
        if rebuilt != ",".join(file_row):
            mismatches.append(f"id {observation_id}: {rebuilt} != {','.join(file_row)}")
            if len(mismatches) >= 3:
                break
    report.add(
        "payload.roundtrip_matches_file",
        not mismatches,
        "; ".join(mismatches) or f"{len(feature_rows)} linhas re-serializam byte a byte",
    )

    names, body = smoke_payload(cfg)
    lines = body.split("\n")
    report.add(
        "payload.smoke_shape",
        len(lines) == len(SMOKE_RECORDS)
        and all(len(line.split(",")) == len(cfg.feature_order) for line in lines),
        f"registros de smoke {names} -> {len(lines)} linhas de {len(cfg.feature_order)} colunas",
    )


def validate(cfg: LabConfig, schema: dict[str, Any], data: Path) -> Report:
    """Roda todas as verificações e devolve o relatório (nunca levanta exceção por violação)."""
    report = Report()
    required = [
        SOURCE_FILE,
        MODEL_TRAIN_FILE,
        MODEL_VALIDATION_FILE,
        MODEL_TEST_FEATURES_FILE,
        TEST_LABELS_FILE,
        MANIFEST_FILE,
    ]
    missing = [name for name in required if not (data / name).exists()]
    report.add(
        "files.present",
        not missing,
        f"faltando {missing} em {data}" if missing else f"todos os {len(required)} arquivos presentes em {data}",
    )
    if missing:
        return report

    report.add(
        "schema.serving_contract",
        schema["serving"]["column_count"] == len(cfg.feature_order)
        and schema["serving"]["header"] is False
        and schema["serving"]["label_present"] is False,
        f"serving: {schema['serving']}",
    )
    report.add(
        "schema.training_contract",
        schema["training"]["column_count"] == len(cfg.feature_order) + 1
        and schema["training"]["header"] is False
        and schema["training"]["label_position"] == "first",
        f"training: {schema['training']}",
    )
    report.add(
        "schema.feature_order_matches_config",
        schema["feature_order"] == cfg.feature_order,
        "schema.json e lab.yaml concordam na ordem das features",
    )

    check_source(cfg, schema, data, report)
    check_model_files(cfg, data, report)
    check_split_integrity(cfg, data, report)
    check_payload_contract(cfg, data, report)
    return report
