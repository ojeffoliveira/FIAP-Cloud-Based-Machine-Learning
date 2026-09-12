"""Métricas de avaliação implementadas de forma transparente.

Por que não chamar o scikit-learn direto e pronto: a aula defende que "correto" é
uma cadeia de evidências. Uma métrica que você não consegue abrir é um elo que
você não consegue inspecionar. Então os números que o lab reporta vêm das funções
abaixo, e `scripts/evaluate_endpoint.py` os confere contra o scikit-learn em
tempo de execução - se os dois discordarem, essa discordância também é registrada
como evidência.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

import numpy as np


@dataclass(frozen=True)
class ConfusionMatrix:
    true_negative: int
    false_positive: int
    false_negative: int
    true_positive: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _as_arrays(y_true: Sequence[float], y_score: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    truth = np.asarray(y_true, dtype=np.int64)
    score = np.asarray(y_score, dtype=np.float64)
    if truth.shape != score.shape:
        raise ValueError(f"tamanhos diferentes: y_true={truth.shape} y_score={score.shape}")
    if truth.size == 0:
        raise ValueError("não é possível calcular métricas num vetor vazio")
    invalid = set(np.unique(truth).tolist()) - {0, 1}
    if invalid:
        raise ValueError(f"y_true precisa ser binário 0/1, encontrei {sorted(invalid)}")
    if not np.all(np.isfinite(score)):
        raise ValueError("y_score contém valores não finitos")
    return truth, score


def confusion_matrix(y_true: Sequence[int], y_pred: Sequence[int]) -> ConfusionMatrix:
    truth = np.asarray(y_true, dtype=np.int64)
    pred = np.asarray(y_pred, dtype=np.int64)
    if truth.shape != pred.shape:
        raise ValueError(f"tamanhos diferentes: y_true={truth.shape} y_pred={pred.shape}")
    return ConfusionMatrix(
        true_negative=int(np.sum((truth == 0) & (pred == 0))),
        false_positive=int(np.sum((truth == 0) & (pred == 1))),
        false_negative=int(np.sum((truth == 1) & (pred == 0))),
        true_positive=int(np.sum((truth == 1) & (pred == 1))),
    )


def accuracy(cm: ConfusionMatrix) -> float:
    total = cm.true_negative + cm.false_positive + cm.false_negative + cm.true_positive
    return (cm.true_positive + cm.true_negative) / total if total else 0.0


def precision(cm: ConfusionMatrix) -> float:
    predicted_positive = cm.true_positive + cm.false_positive
    return cm.true_positive / predicted_positive if predicted_positive else 0.0


def recall(cm: ConfusionMatrix) -> float:
    actual_positive = cm.true_positive + cm.false_negative
    return cm.true_positive / actual_positive if actual_positive else 0.0


def f1(cm: ConfusionMatrix) -> float:
    p, r = precision(cm), recall(cm)
    return 2 * p * r / (p + r) if (p + r) else 0.0


def roc_auc(y_true: Sequence[int], y_score: Sequence[float]) -> float:
    """AUC por ranking (Mann-Whitney U), com rank médio para escores empatados."""
    truth, score = _as_arrays(y_true, y_score)
    n_positive = int(truth.sum())
    n_negative = int(truth.size - n_positive)
    if n_positive == 0 or n_negative == 0:
        raise ValueError("ROC-AUC é indefinido quando só uma classe está presente")

    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(score.size, dtype=np.float64)
    sorted_scores = score[order]
    i = 0
    while i < sorted_scores.size:
        j = i
        while j + 1 < sorted_scores.size and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1

    sum_positive_ranks = float(ranks[truth == 1].sum())
    return (sum_positive_ranks - n_positive * (n_positive + 1) / 2.0) / (n_positive * n_negative)


def average_precision(y_true: Sequence[int], y_score: Sequence[float]) -> float:
    """PR-AUC como a soma em degraus usada pelo average_precision_score do scikit-learn."""
    truth, score = _as_arrays(y_true, y_score)
    order = np.argsort(-score, kind="mergesort")
    truth_sorted = truth[order]
    cumulative_tp = np.cumsum(truth_sorted)
    positions = np.arange(1, truth.size + 1, dtype=np.float64)
    precision_at_k = cumulative_tp / positions
    n_positive = int(truth.sum())
    if n_positive == 0:
        raise ValueError("average precision é indefinida sem amostras positivas")
    return float(np.sum(precision_at_k * truth_sorted) / n_positive)


def brier_score(y_true: Sequence[int], y_score: Sequence[float]) -> float:
    truth, score = _as_arrays(y_true, y_score)
    return float(np.mean((score - truth) ** 2))


def prevalence(y_true: Sequence[int]) -> float:
    truth = np.asarray(y_true, dtype=np.int64)
    return float(truth.mean())


def majority_baseline_accuracy(y_true: Sequence[int]) -> float:
    """Acurácia de sempre prever a classe majoritária - a régua a ser batida."""
    p = prevalence(y_true)
    return max(p, 1.0 - p)


def calibration_bins(
    y_true: Sequence[int], y_score: Sequence[float], bins: int = 5
) -> list[dict[str, Any]]:
    """Diagnóstico grosso de confiabilidade: taxa prevista vs observada por faixa de escore."""
    truth, score = _as_arrays(y_true, y_score)
    edges = np.linspace(0.0, 1.0, bins + 1)
    report: list[dict[str, Any]] = []
    for k in range(bins):
        lo, hi = edges[k], edges[k + 1]
        mask = (score >= lo) & (score < hi) if k < bins - 1 else (score >= lo) & (score <= hi)
        count = int(mask.sum())
        report.append(
            {
                "bin": f"[{lo:.1f},{hi:.1f}{')' if k < bins - 1 else ']'}",
                "count": count,
                "mean_predicted": round(float(score[mask].mean()), 6) if count else None,
                "observed_rate": round(float(truth[mask].mean()), 6) if count else None,
            }
        )
    return report


def evaluate(
    y_true: Sequence[int], y_score: Sequence[float], threshold: float = 0.5
) -> dict[str, Any]:
    """Relatório completo. Acurácia aparece, mas nunca sozinha - a lição é essa."""
    truth, score = _as_arrays(y_true, y_score)
    y_pred = (score >= threshold).astype(np.int64)
    cm = confusion_matrix(truth, y_pred)
    model_accuracy = accuracy(cm)
    baseline = majority_baseline_accuracy(truth)
    return {
        "samples": int(truth.size),
        "decision_threshold": threshold,
        "prevalence": round(prevalence(truth), 6),
        "majority_baseline_accuracy": round(baseline, 6),
        "confusion_matrix": cm.as_dict(),
        "accuracy": round(model_accuracy, 6),
        "precision": round(precision(cm), 6),
        "recall": round(recall(cm), 6),
        "f1": round(f1(cm), 6),
        "roc_auc": round(roc_auc(truth, score), 6),
        "pr_auc": round(average_precision(truth, score), 6),
        "brier_score": round(brier_score(truth, score), 6),
        "beats_majority_baseline": bool(model_accuracy > baseline),
        "accuracy_lift_over_baseline": round(model_accuracy - baseline, 6),
        "calibration": calibration_bins(truth, score),
    }
