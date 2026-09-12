"""Qualidade do modelo — as métricas que só existem quando o rótulo verdadeiro chega.

A separação entre este módulo e `drift.py` é o conteúdo central do laboratório:

* `drift.py` compara distribuições e **nunca olha o rótulo**. Roda no minuto em que
  a predição acontece.
* `evaluation.py` compara predição com verdade. Só roda quando a verdade existe — no
  churn da Bora Fibra, dias depois da predição.

É por isso que drift chega antes e qualidade chega depois, e por isso a decisão
operacional não pode esperar só pela segunda.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


@dataclass(frozen=True)
class QualityReport:
    """Qualidade de uma janela, com a matriz de confusão aberta.

    A matriz aparece destrinchada em quatro números porque é ela que explica o
    modo de falha: um F1 de 0,52 com falso positivo alto e falso negativo baixo é
    um modelo prevendo churn demais — leitura completamente diferente de um F1 de
    0,52 com o desenho inverso.
    """

    window: str
    rows: int
    threshold: float
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float
    true_negatives: int
    false_positives: int
    false_negatives: int
    true_positives: int
    predicted_churn_rate: float
    actual_churn_rate: float
    mean_probability: float

    def as_dict(self) -> dict[str, float | int | str]:
        return asdict(self)

    @property
    def failure_mode(self) -> str:
        """Leitura textual do modo de falha, para o relatório não exigir aritmética."""
        if self.false_positives > self.false_negatives * 2:
            return (
                "prevê churn demais: o número de falsos positivos domina. Campanha de "
                "retenção seria disparada para cliente que ia ficar."
            )
        if self.false_negatives > self.false_positives * 2:
            return (
                "prevê churn de menos: o número de falsos negativos domina. Cliente que "
                "ia sair passaria sem ser abordado."
            )
        return "erros distribuídos entre falso positivo e falso negativo."


def evaluate(
    window: str, scores: np.ndarray, labels: np.ndarray, threshold: float
) -> QualityReport:
    """Calcula a qualidade de uma janela a partir dos scores e dos rótulos."""
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=int)

    if scores.shape != labels.shape:
        raise ValueError(
            f"scores ({scores.shape}) e rótulos ({labels.shape}) têm tamanhos diferentes: "
            "a junção por identificador falhou."
        )

    predicted = (scores >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(labels, predicted, labels=[0, 1]).ravel()

    return QualityReport(
        window=window,
        rows=int(labels.size),
        threshold=float(threshold),
        accuracy=float(accuracy_score(labels, predicted)),
        # zero_division=0: uma janela em que o modelo não prevê nenhum positivo tem
        # precisão indefinida, e 0 é a leitura útil aqui — não um aviso no meio da aula.
        precision=float(precision_score(labels, predicted, zero_division=0)),
        recall=float(recall_score(labels, predicted, zero_division=0)),
        f1=float(f1_score(labels, predicted, zero_division=0)),
        roc_auc=float(roc_auc_score(labels, scores)),
        true_negatives=int(tn),
        false_positives=int(fp),
        false_negatives=int(fn),
        true_positives=int(tp),
        predicted_churn_rate=float(predicted.mean()),
        actual_churn_rate=float(labels.mean()),
        mean_probability=float(scores.mean()),
    )


def compare(baseline: QualityReport, observed: QualityReport) -> dict[str, object]:
    """Compara duas janelas e diz se a mudança de distribuição virou perda de qualidade."""
    queda_f1 = baseline.f1 - observed.f1
    queda_auc = baseline.roc_auc - observed.roc_auc

    return {
        "janela_referencia": baseline.window,
        "janela_observada": observed.window,
        "f1_referencia": round(baseline.f1, 6),
        "f1_observado": round(observed.f1, 6),
        "queda_f1": round(queda_f1, 6),
        "roc_auc_referencia": round(baseline.roc_auc, 6),
        "roc_auc_observado": round(observed.roc_auc, 6),
        "queda_roc_auc": round(queda_auc, 6),
        "modo_de_falha": observed.failure_mode,
        # O veredito é textual e conservador de propósito: o número decide, mas quem
        # aprova retraining é uma pessoa.
        "leitura": (
            "o drift de dados veio acompanhado de perda de qualidade medida"
            if queda_f1 > 0
            else "o drift de dados NÃO veio acompanhado de perda de qualidade medida"
        ),
    }
