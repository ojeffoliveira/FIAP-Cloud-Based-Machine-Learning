"""Qualidade do modelo -- so existe quando o rotulo verdadeiro (ground truth)
chega, dias depois da predicao. Mesma separacao de papel do lab
`04-ml-operations/01-observability-drift-response`: `drift.py` compara
distribuicao e nunca olha o rotulo; este modulo compara predicao com verdade.

Junta as predicoes das duas janelas de producao com
`ground_truth_baseline.csv`/`ground_truth_shifted.csv` pela chave
`observation_id`, calcula F1/ROC-AUC/matriz de confusao por janela e publica
`ModelQualityF1`/`ModelQualityROCAUC`. So descreve numero e direcao do erro
-- nao decide go-live, retraining ou rollback: essa leitura e do aluno, no
`DECISION.md`.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import confusion_matrix, f1_score, roc_auc_score

from . import serving
from .aws import log, put_metric_data
from .config import Config


@dataclass(frozen=True)
class QualityReport:
    """Qualidade de uma janela, com a matriz de confusao aberta -- e ela que
    mostra em qual direcao o modelo passou a errar, nao so quanto o F1 caiu."""

    window: str
    rows: int
    threshold: float
    f1: float
    roc_auc: float
    true_negatives: int
    false_positives: int
    false_negatives: int
    true_positives: int
    predicted_churn_rate: float
    actual_churn_rate: float

    def as_dict(self) -> dict[str, float | int | str]:
        return asdict(self)


def _read_ground_truth(path: Path) -> dict[str, int]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return {row["observation_id"]: int(row["churn"]) for row in reader}


def _score_with_ids(cfg: Config, csv_path: Path) -> dict[str, float]:
    """Probabilidade de churn por `observation_id`, via endpoint Real-Time --
    mesmo candidato usado por `drift.py` para pontuar trafego de producao."""
    outputs = serving.terraform_outputs(cfg)
    endpoint_name = serving.require_output(outputs, "realtime_endpoint_name")
    ids, rows = serving.build_payload_rows(csv_path, cfg)

    scores: dict[str, float] = {}
    batch_size = 100
    for start in range(0, len(rows), batch_size):
        chunk_ids = ids[start : start + batch_size]
        chunk_rows = rows[start : start + batch_size]
        body = "\n".join(chunk_rows) + "\n"
        result = serving.invoke_realtime(endpoint_name, body, cfg.region)
        for observation_id, probability in zip(chunk_ids, result.probabilities):
            scores[observation_id] = probability
    return scores


def evaluate(window: str, scores_by_id: dict[str, float], labels_by_id: dict[str, int], threshold: float) -> QualityReport:
    """Junta scores e rotulos pela mesma chave (`observation_id`) -- nunca
    por posicao, porque nada garante que as duas fontes vieram na mesma
    ordem."""
    shared_ids = sorted(set(scores_by_id) & set(labels_by_id))
    missing = sorted(set(labels_by_id) - set(scores_by_id))
    if missing:
        raise ValueError(
            f"janela {window}: {len(missing)} observation_id(s) do ground truth sem predicao "
            f"correspondente (ex.: {missing[:3]}). Cobertura completa e exigida pelo contrato "
            "de dados."
        )
    if not shared_ids:
        raise ValueError(f"janela {window}: nenhum observation_id em comum entre predicao e ground truth.")

    scores = np.array([scores_by_id[i] for i in shared_ids], dtype=float)
    labels = np.array([labels_by_id[i] for i in shared_ids], dtype=int)
    predicted = (scores >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(labels, predicted, labels=[0, 1]).ravel()

    return QualityReport(
        window=window,
        rows=int(labels.size),
        threshold=float(threshold),
        f1=float(f1_score(labels, predicted, zero_division=0)),
        roc_auc=float(roc_auc_score(labels, scores)),
        true_negatives=int(tn),
        false_positives=int(fp),
        false_negatives=int(fn),
        true_positives=int(tp),
        predicted_churn_rate=float(predicted.mean()),
        actual_churn_rate=float(labels.mean()),
    )


def failure_direction(report: QualityReport) -> str:
    """Rotulo textual da direcao do erro -- so descreve o desenho da matriz,
    nunca prescreve uma acao (nao fala de campanha, retraining ou rollback)."""
    if report.false_positives > report.false_negatives * 2:
        return "falso_positivo"
    if report.false_negatives > report.false_positives * 2:
        return "falso_negativo"
    return "distribuido"


def _window_dict(report: QualityReport) -> dict[str, Any]:
    return {
        "f1": round(report.f1, 6),
        "roc_auc": round(report.roc_auc, 6),
        "predicted_churn_rate": round(report.predicted_churn_rate, 6),
        "actual_churn_rate": round(report.actual_churn_rate, 6),
        "confusion_matrix": {
            "true_negative": report.true_negatives,
            "false_positive": report.false_positives,
            "false_negative": report.false_negatives,
            "true_positive": report.true_positives,
        },
        "rows": report.rows,
        "threshold": report.threshold,
    }


def cmd_ground_truth(cfg: Config, args: Any) -> int:
    threshold = float(cfg.acceptance.get("quality", {}).get("decision_threshold", 0.5))

    baseline_scores = _score_with_ids(cfg, cfg.data_dir / "production_baseline.csv")
    shifted_scores = _score_with_ids(cfg, cfg.data_dir / "production_shifted.csv")
    baseline_labels = _read_ground_truth(cfg.data_dir / "ground_truth_baseline.csv")
    shifted_labels = _read_ground_truth(cfg.data_dir / "ground_truth_shifted.csv")

    baseline_report = evaluate("baseline", baseline_scores, baseline_labels, threshold)
    shifted_report = evaluate("shifted", shifted_scores, shifted_labels, threshold)

    for report in (baseline_report, shifted_report):
        log(
            f"[ground-truth] {report.window}: f1={report.f1:.6f} roc_auc={report.roc_auc:.6f} "
            f"cm=[[{report.true_negatives},{report.false_positives}],"
            f"[{report.false_negatives},{report.true_positives}]]"
        )

    payload = {
        "windows": {
            "baseline": _window_dict(baseline_report),
            "shifted": _window_dict(shifted_report),
        },
        "comparison": {
            "f1_drop": round(baseline_report.f1 - shifted_report.f1, 6),
            "roc_auc_drop": round(baseline_report.roc_auc - shifted_report.roc_auc, 6),
            "failure_mode": failure_direction(shifted_report),
        },
    }

    # Dimensao fixada por A6a em PEDIDOS.md: o dashboard le ModelQualityF1/
    # ModelQualityROCAUC dimensionadas so por `Window` ("baseline"/"shifted"),
    # nunca por `EndpointName` -- mesma convencao usada em drift.py.
    for report in (baseline_report, shifted_report):
        dims = {"Window": report.window}
        put_metric_data(cfg.namespace, "ModelQualityF1", report.f1, cfg.region, dimensions=dims)
        put_metric_data(cfg.namespace, "ModelQualityROCAUC", report.roc_auc, cfg.region, dimensions=dims)
    log(f"[ground-truth] metricas publicadas em {cfg.namespace}")

    evidence_path = cfg.evidence_dir / "quality.json"
    evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    log(f"[ground-truth] evidencia em {evidence_path}")

    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0
