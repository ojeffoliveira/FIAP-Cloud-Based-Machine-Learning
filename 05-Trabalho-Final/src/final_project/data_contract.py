"""Contrato de dados executável do Trabalho Final — Bora Fibra.

Não é texto de README: é o conjunto de verificações abaixo, rodado por
`make validate-data` **antes de qualquer recurso faturável da AWS**. Um
dataset que reprova aqui nunca gera um training job — então nenhum aluno
paga compute por um dado que ninguém conferiu (Regra de ouro do
`03_BOOTSTRAP_AND_CODE_CONTRACT.md`).

Os limiares vêm de `config/acceptance.yaml`; a ordem de features, o nome do
target/id e os bounds de geração vêm de `config/scenario.yaml` via
`Config`. Este módulo nunca hardcoda um limiar — se um número precisar
mudar, a mudança é no YAML, não aqui.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .dataset import FILES, MANIFEST_FILE, _center, _relationship_from_dict, fingerprints, systematic_probability

# Arquivos que carregam cabeçalho (id + colunas nomeadas). Os dois que faltam
# em FILES — train.csv e validation.csv — são o canal headerless do XGBoost
# nativo: rótulo na primeira coluna, sem id, sem cabeçalho.
HEADER_FILES: frozenset[str] = frozenset(
    {
        "reference.csv",
        "production_baseline.csv",
        "production_shifted.csv",
        "campaign_features.csv",
        "ground_truth_baseline.csv",
        "ground_truth_shifted.csv",
        "atendimento.csv",
    }
)

# Arquivos de payload de inferência puro: id + features, nunca rótulo. É o
# contrato que `make atendimento`/`make campanha` (A5) consomem depois de
# tirar a coluna de id.
INFERENCE_FILES: tuple[str, ...] = (
    "production_baseline.csv",
    "production_shifted.csv",
    "campaign_features.csv",
    "atendimento.csv",
)

GROUND_TRUTH_FILES: tuple[tuple[str, str], ...] = (
    ("production_baseline.csv", "ground_truth_baseline.csv"),
    ("production_shifted.csv", "ground_truth_shifted.csv"),
)


def log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


def emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


class ContractError(AssertionError):
    """Levantada só quando uma verificação não consegue nem tentar rodar
    (arquivo ausente); toda violação verificável vira um `Check` reprovado,
    nunca uma exc}eção — o relatório precisa sair completo mesmo com falha."""


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
        return len(self.checks) > 0 and not self.failed

    def as_dict(self) -> dict[str, Any]:
        return {
            "checks_total": len(self.checks),
            "checks_failed": len(self.failed),
            "passed": self.ok,
            "checks": [c.as_dict() for c in self.checks],
        }


# --------------------------------------------------------------------------- #
# Leitura de CSV mínima — sem pandas, para o contrato não ganhar dependência
# só para ler um arquivo que ele mesmo define o formato.
# --------------------------------------------------------------------------- #


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.reader(handle) if row]


def _is_float(text: str) -> bool:
    try:
        return math.isfinite(float(text))
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Verificações
# --------------------------------------------------------------------------- #


def check_files_present(cfg: Config, report: Report) -> bool:
    required = [*FILES, MANIFEST_FILE]
    missing = [name for name in required if not (cfg.data_dir / name).exists()]
    report.add(
        "files.present",
        not missing,
        f"faltando {missing} em {cfg.data_dir}" if missing else f"todos os {len(required)} arquivos presentes",
    )
    return not missing


def check_row_counts(cfg: Config, report: Report) -> None:
    expected = cfg.acceptance["row_counts"]
    for filename, expected_rows in expected.items():
        rows = _read_rows(cfg.data_dir / filename)
        data_rows = len(rows) - 1 if filename in HEADER_FILES else len(rows)
        report.add(
            f"row_count.{filename}",
            data_rows == int(expected_rows),
            f"{data_rows} linhas de dados, esperado {expected_rows}",
        )


def check_schema(cfg: Config, report: Report) -> dict[str, list[list[str]]]:
    """Confere cabeçalho/coluna de cada arquivo e devolve as linhas de dados
    já lidas (sem cabeçalho), para as próximas verificações reaproveitarem
    sem reabrir o arquivo do disco."""
    features = cfg.feature_order
    parsed: dict[str, list[list[str]]] = {}

    for filename in ("train.csv", "validation.csv"):
        rows = _read_rows(cfg.data_dir / filename)
        parsed[filename] = rows
        widths = {len(r) for r in rows}
        report.add(
            f"schema.{filename}.no_header",
            bool(rows) and all(_is_float(cell) for cell in rows[0]),
            f"primeira linha é numérica (sem cabeçalho): {rows[0][:3] if rows else 'VAZIO'}",
        )
        report.add(
            f"schema.{filename}.column_count",
            widths == {len(features) + 1},
            f"larguras {sorted(widths)}, esperado {{{len(features) + 1}}} (rótulo + {len(features)} features)",
        )
        first_column = {r[0] for r in rows}
        report.add(
            f"schema.{filename}.label_first_binary",
            first_column <= {"0", "1"},
            f"valores da coluna 0 (rótulo): {sorted(first_column)}",
        )

    for filename in ("reference.csv",):
        all_rows = _read_rows(cfg.data_dir / filename)
        header, rows = all_rows[0], all_rows[1:]
        parsed[filename] = rows
        expected_header = [cfg.id_column, *features, cfg.label_column]
        report.add(f"schema.{filename}.header", header == expected_header, f"{header} vs esperado {expected_header}")

    for filename in INFERENCE_FILES:
        all_rows = _read_rows(cfg.data_dir / filename)
        header, rows = all_rows[0], all_rows[1:]
        parsed[filename] = rows
        expected_header = [cfg.id_column, *features]
        report.add(f"schema.{filename}.header", header == expected_header, f"{header} vs esperado {expected_header}")

    for filename in ("ground_truth_baseline.csv", "ground_truth_shifted.csv"):
        all_rows = _read_rows(cfg.data_dir / filename)
        header, rows = all_rows[0], all_rows[1:]
        parsed[filename] = rows
        expected_header = [cfg.id_column, cfg.label_column]
        report.add(f"schema.{filename}.header", header == expected_header, f"{header} vs esperado {expected_header}")

    return parsed


def check_no_nan_or_inf(cfg: Config, parsed: dict[str, list[list[str]]], report: Report) -> None:
    """Só as colunas numéricas entram nesta verificação — arquivos com
    cabeçalho trazem `observation_id` na coluna 0, que é string por
    contrato (nunca vira feature), não um NaN disfarçado."""
    for filename, rows in parsed.items():
        first_numeric_column = 1 if filename in HEADER_FILES else 0
        offenders: list[str] = []
        for line_number, row in enumerate(rows, start=2):
            for cell in row[first_numeric_column:]:
                if not _is_float(cell):
                    offenders.append(f"linha {line_number}: {cell!r}")
                    if len(offenders) >= 5:
                        break
            if len(offenders) >= 5:
                break
        report.add(
            f"finite.{filename}",
            not offenders,
            "; ".join(offenders) if offenders else "toda célula numérica converte para float finito",
        )


def _feature_columns(filename: str, cfg: Config) -> dict[str, int] | None:
    """Índice de cada feature dentro das linhas JÁ SEM cabeçalho de `parsed`,
    ou None se o arquivo não carrega as sete features (ground truth puro)."""
    features = cfg.feature_order
    if filename in ("train.csv", "validation.csv"):
        return {f: i + 1 for i, f in enumerate(features)}  # coluna 0 é o rótulo
    if filename == "reference.csv" or filename in INFERENCE_FILES:
        return {f: i + 1 for i, f in enumerate(features)}  # coluna 0 é o id
    return None


def check_ranges(cfg: Config, parsed: dict[str, list[list[str]]], report: Report) -> None:
    ranges = {k: (float(v[0]), float(v[1])) for k, v in cfg.acceptance["ranges"].items()}
    for filename, rows in parsed.items():
        columns = _feature_columns(filename, cfg)
        if columns is None:
            continue
        out_of_range: list[str] = []
        for line_number, row in enumerate(rows, start=2):
            for feature, col in columns.items():
                value = float(row[col])
                low, high = ranges[feature]
                if not (low <= value <= high):
                    out_of_range.append(f"linha {line_number} {feature}={value} fora de [{low},{high}]")
        report.add(
            f"ranges.{filename}",
            not out_of_range,
            "; ".join(out_of_range[:5]) if out_of_range else "todas as features dentro dos limites do contrato",
        )


def check_labels(cfg: Config, parsed: dict[str, list[list[str]]], report: Report) -> None:
    label_sources = {
        "train.csv": lambda row: row[0],
        "validation.csv": lambda row: row[0],
        "reference.csv": lambda row: row[-1],
        "ground_truth_baseline.csv": lambda row: row[-1],
        "ground_truth_shifted.csv": lambda row: row[-1],
    }
    for filename, extract in label_sources.items():
        values = {extract(row) for row in parsed[filename]}
        report.add(
            f"labels.{filename}",
            values <= {"0", "1"},
            f"valores de rótulo presentes: {sorted(values)}",
        )


def check_unique_ids(cfg: Config, parsed: dict[str, list[list[str]]], report: Report) -> dict[str, list[str]]:
    id_bearing = ("reference.csv", *INFERENCE_FILES, "ground_truth_baseline.csv", "ground_truth_shifted.csv")
    ids_by_file: dict[str, list[str]] = {}
    for filename in id_bearing:
        ids = [row[0] for row in parsed[filename]]
        ids_by_file[filename] = ids
        report.add(
            f"unique_ids.{filename}",
            len(set(ids)) == len(ids),
            f"{len(ids) - len(set(ids))} ids duplicados" if len(set(ids)) != len(ids) else f"{len(ids)} ids únicos",
        )
    return ids_by_file


def check_inference_payload(cfg: Config, parsed: dict[str, list[list[str]]], report: Report) -> None:
    """O payload que vai para o endpoint/batch não pode carregar id nem
    rótulo. A verificação estrutural (header) já provou isso; aqui a
    intenção fica explícita e nomeada, porque é a regra mais cara de violar
    em silêncio: um rótulo vazando para dentro do payload de inferência não
    dá erro nenhum, só um resultado que parece bom demais."""
    for filename in INFERENCE_FILES:
        rows = parsed[filename]
        widths = {len(r) for r in rows}
        n_features = len(cfg.feature_order)
        report.add(
            f"payload.{filename}.no_target_no_id_leak",
            widths == {n_features + 1},
            f"larguras {sorted(widths)}: 1 coluna de id + {n_features} features, sem rótulo",
        )


def check_fingerprints(cfg: Config, report: Report) -> None:
    manifest_path = cfg.data_dir / MANIFEST_FILE
    if not manifest_path.exists():
        report.add("fingerprints.manifest_present", False, str(manifest_path))
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded = {name: info["sha256"] for name, info in manifest.get("files", {}).items()}
    current = fingerprints(cfg)

    report.add(
        "fingerprints.manifest_present",
        set(recorded) >= set(FILES),
        f"{len(recorded)} hashes registrados no manifesto",
    )
    mismatched = [name for name in FILES if recorded.get(name) != current.get(name)]
    report.add(
        "fingerprints.match_manifest",
        not mismatched,
        f"divergentes de {manifest_path.name}: {mismatched}" if mismatched else "todos os arquivos batem com o manifesto — ninguém editou um CSV depois de gerado",
    )
    report.add(
        "fingerprints.feature_order_unchanged",
        manifest.get("feature_order") == cfg.feature_order,
        f"{manifest.get('feature_order')} vs config atual {cfg.feature_order}",
    )
    report.add(
        "fingerprints.seed_unchanged",
        manifest.get("seed") == cfg.seed,
        f"semente do manifesto {manifest.get('seed')} vs config atual {cfg.seed}",
    )


def check_workload_counts(cfg: Config, parsed: dict[str, list[list[str]]], report: Report) -> None:
    workload = cfg.acceptance["workload_counts"]
    report.add(
        "workload.campanha_output_count",
        len(parsed["campaign_features.csv"]) == int(workload["campanha_output_count"]),
        f"{len(parsed['campaign_features.csv'])} clientes na lista de campanha, esperado {workload['campanha_output_count']}",
    )
    report.add(
        "workload.atendimento_output_count",
        len(parsed["atendimento.csv"]) == int(workload["atendimento_output_count"]),
        f"{len(parsed['atendimento.csv'])} clientes de atendimento, esperado {workload['atendimento_output_count']}",
    )


def check_ground_truth_coverage(cfg: Config, ids_by_file: dict[str, list[str]], report: Report) -> None:
    for production_file, ground_truth_file in GROUND_TRUTH_FILES:
        production_ids = set(ids_by_file[production_file])
        ground_truth_ids = set(ids_by_file[ground_truth_file])
        missing = production_ids - ground_truth_ids
        extra = ground_truth_ids - production_ids
        report.add(
            f"ground_truth_coverage.{production_file}",
            not missing and not extra,
            (
                "cobertura completa: todo observation_id tem rótulo atrasado"
                if not missing and not extra
                else f"faltando rótulo para {len(missing)} ids; {len(extra)} rótulos sem cliente correspondente"
            ),
        )


def check_atendimento_risk_separation(cfg: Config, parsed: dict[str, list[list[str]]], report: Report) -> None:
    """Sanity-check pedido pelo enunciado: os 40 clientes de atendimento
    precisam ter pelo menos um caso de risco alto e um de risco baixo
    claramente separados — calculado sem depender de um modelo treinado,
    pela probabilidade sistemática da relação `base` do gerador."""
    gates = cfg.acceptance["atendimento"]
    features = cfg.feature_order
    rows = parsed["atendimento.csv"]
    feats = {f: [] for f in features}
    for row in rows:
        for i, f in enumerate(features, start=1):
            feats[f].append(float(row[i]))
    import numpy as np

    feats_arr = {f: np.array(v) for f, v in feats.items()}
    rel = _relationship_from_dict(cfg.scenario["relationship"]["base"])
    center = _center(cfg)
    probability = systematic_probability(feats_arr, rel, center)

    high = float(probability.max())
    low = float(probability.min())
    spread = high - low
    report.add(
        "atendimento.risk_separation",
        high >= float(gates["high_risk_probability_at_least"])
        and low <= float(gates["low_risk_probability_at_most"])
        and spread >= float(gates["min_probability_spread"]),
        f"probabilidade sistemática min={low:.4f} max={high:.4f} spread={spread:.4f}",
    )


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #


def validate(cfg: Config) -> Report:
    """Roda todas as verificações e devolve o relatório. Nunca levanta
    exceção por violação de contrato — só por arquivo ausente, que impede o
    resto de rodar."""
    report = Report()
    if not check_files_present(cfg, report):
        return report

    check_row_counts(cfg, report)
    parsed = check_schema(cfg, report)
    check_no_nan_or_inf(cfg, parsed, report)
    check_ranges(cfg, parsed, report)
    check_labels(cfg, parsed, report)
    ids_by_file = check_unique_ids(cfg, parsed, report)
    check_inference_payload(cfg, parsed, report)
    check_fingerprints(cfg, report)
    check_workload_counts(cfg, parsed, report)
    check_ground_truth_coverage(cfg, ids_by_file, report)
    check_atendimento_risk_separation(cfg, parsed, report)
    return report


def cmd_validate_data(cfg: Config, args: Any) -> int:
    """`make validate-data`: roda o contrato, grava
    `artifacts/evidence/data-contract.json` e sai != 0 se algo reprovar —
    antes de qualquer recurso faturável ser criado."""
    report = validate(cfg)

    log(f"contrato de dados: {len(report.checks)} verificações contra {cfg.data_dir}")
    for check in report.checks:
        log(f"  [{'PASS' if check.passed else 'FAIL'}] {check.name}: {check.detail}")
    log(f"[{'PASS' if report.ok else 'FAIL'}] {len(report.failed)} de {len(report.checks)} verificações reprovaram")

    payload = report.as_dict()
    cfg.evidence_dir.mkdir(parents=True, exist_ok=True)
    (cfg.evidence_dir / "data-contract.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    emit(payload)
    return 0 if report.ok else 1
