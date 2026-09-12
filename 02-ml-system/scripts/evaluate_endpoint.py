#!/usr/bin/env python3
"""Avalia o conjunto de teste separado através do endpoint publicado.

O conjunto de teste é pontuado onde importa - pela rede, pelo mesmo caminho de
serving que um chamador usaria - e não em memória contra um objeto de modelo
local. É essa a diferença entre "o modelo funciona" e "o sistema funciona".

A acurácia é reportada ao lado da baseline da classe majoritária de propósito: num
dataset com ~34% de positivos, prever "nunca cancela" já acerta ~66%.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab1 import aws_helpers as aws
from lab1 import metrics as m
from lab1.config import (
    DATA_DIR,
    MODEL_TEST_FEATURES_FILE,
    TEST_LABELS_FILE,
    emit,
    evidence_dir,
    load_config,
    log,
)


def read_test_set(data: Path) -> tuple[list[str], list[int], list[int]]:
    with (data / MODEL_TEST_FEATURES_FILE).open(newline="", encoding="utf-8") as handle:
        rows = [",".join(row) for row in csv.reader(handle) if row]
    with (data / TEST_LABELS_FILE).open(newline="", encoding="utf-8") as handle:
        label_rows = [row for row in csv.reader(handle) if row][1:]
    ids = [int(row[0]) for row in label_rows]
    labels = [int(row[1]) for row in label_rows]
    if not (len(rows) == len(labels)):
        raise aws.AwsError(f"{len(rows)} linhas de features e {len(labels)} rótulos - dataset inconsistente")
    return rows, labels, ids


def cross_check_with_sklearn(labels: list[int], scores: list[float], report: dict) -> dict:
    """Segunda opinião independente sobre o nosso próprio código de métricas."""
    try:
        from sklearn.metrics import (
            accuracy_score,
            f1_score,
            precision_score,
            recall_score,
            roc_auc_score,
        )
    except ImportError:
        return {"available": False}

    threshold = report["decision_threshold"]
    predictions = [1 if s >= threshold else 0 for s in scores]
    reference = {
        "accuracy": float(accuracy_score(labels, predictions)),
        "precision": float(precision_score(labels, predictions, zero_division=0)),
        "recall": float(recall_score(labels, predictions, zero_division=0)),
        "f1": float(f1_score(labels, predictions, zero_division=0)),
        "roc_auc": float(roc_auc_score(labels, scores)),
    }
    deltas = {k: abs(reference[k] - report[k]) for k in reference}
    return {
        "available": True,
        "reference": {k: round(v, 6) for k, v in reference.items()},
        "max_absolute_delta": round(max(deltas.values()), 9),
        "agrees": all(d <= 1e-6 for d in deltas.values()),
    }


def to_markdown(result: dict) -> str:
    r = result["metrics"]
    cm = r["confusion_matrix"]
    accepted = result["acceptance"]
    lines = [
        "# Lab 02 - avaliação no conjunto de teste",
        "",
        f"- Endpoint: `{result['endpoint_name']}`",
        f"- Amostras: {r['samples']}",
        f"- Prevalência da classe positiva: {r['prevalence']:.4f}",
        f"- Limiar de decisão: {r['decision_threshold']} "
        "(fixo por motivo didático, não é uma escolha de produção)",
        f"- Requisições enviadas: {result['requests_sent']} lotes de até {result['batch_size']} linhas",
        "",
        "## Por que acurácia sozinha não responde",
        "",
        f"| Preditor | Acurácia |",
        f"|---|---|",
        f"| Sempre prever a classe majoritária | {r['majority_baseline_accuracy']:.4f} |",
        f"| Modelo publicado | {r['accuracy']:.4f} |",
        "",
        f"Ganho sobre a baseline: **{r['accuracy_lift_over_baseline']:+.4f}**.",
        "",
        "## Matriz de confusão",
        "",
        "| | Previsto 0 | Previsto 1 |",
        "|---|---|---|",
        f"| **Real 0** | {cm['true_negative']} | {cm['false_positive']} |",
        f"| **Real 1** | {cm['false_negative']} | {cm['true_positive']} |",
        "",
        "## Métricas",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Acurácia | {r['accuracy']:.4f} |",
        f"| Precisão | {r['precision']:.4f} |",
        f"| Recall | {r['recall']:.4f} |",
        f"| F1 | {r['f1']:.4f} |",
        f"| ROC-AUC | {r['roc_auc']:.4f} |",
        f"| PR-AUC | {r['pr_auc']:.4f} |",
        f"| Brier score | {r['brier_score']:.4f} |",
        "",
        "## Calibração (diagnóstico)",
        "",
        "| Faixa de escore | Linhas | Média prevista | Taxa observada |",
        "|---|---|---|---|",
    ]
    for row in r["calibration"]:
        predicted = f"{row['mean_predicted']:.4f}" if row["mean_predicted"] is not None else "-"
        observed = f"{row['observed_rate']:.4f}" if row["observed_rate"] is not None else "-"
        lines.append(f"| {row['bin']} | {row['count']} | {predicted} | {observed} |")

    lines += [
        "",
        "## Aceitação",
        "",
        "| Critério | Limiar | Observado | Resultado |",
        "|---|---|---|---|",
    ]
    for name, check in accepted["checks"].items():
        lines.append(
            f"| {name} | {check['threshold']} | {check['observed']} "
            f"| {'PASS' if check['passed'] else 'FAIL'} |"
        )
    lines += [
        "",
        f"**Resultado geral: {'PASS' if accepted['passed'] else 'FAIL'}**",
        "",
    ]
    check = result["sklearn_cross_check"]
    if check.get("available"):
        lines += [
            "## Conferência cruzada das métricas",
            "",
            f"As implementações de métrica do próprio lab concordam com o scikit-learn "
            f"(diferença absoluta máxima {check['max_absolute_delta']:.2e}): "
            f"**{'sim' if check['agrees'] else 'não'}**.",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--endpoint", help="por padrão, o output endpoint_name do Terraform")
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args()

    cfg = load_config()
    try:
        session = aws.make_session(cfg.region, args.profile)
        endpoint = args.endpoint or aws.require_output(aws.terraform_outputs(), "endpoint_name")

        status = aws.describe_endpoint(session, endpoint)["EndpointStatus"]
        if status != "InService":
            raise aws.AwsError(f"o endpoint {endpoint} está {status}, não InService")

        rows, labels, ids = read_test_set(args.data)
        log(f"[evaluate] pontuando {len(rows)} linhas separadas através de {endpoint}")

        scores: list[float] = []
        batches = 0
        for batch in aws.batched(rows, args.batch_size):
            scores.extend(aws.invoke_endpoint_csv(session, endpoint, "\n".join(batch)))
            batches += 1
            log(f"[evaluate] lote {batches}: {len(scores)}/{len(rows)} linhas pontuadas")

        if len(scores) != len(rows):
            raise aws.AwsError(f"{len(scores)} probabilidades para {len(rows)} linhas")
    except aws.AwsError as exc:
        log(f"[FAIL] {exc}")
        emit({"passed": False, "error": str(exc)})
        return 1

    report = m.evaluate(labels, scores, threshold=cfg.decision_threshold)
    acceptance = cfg.acceptance
    checks = {
        "roc_auc_min": {
            "threshold": acceptance["roc_auc_min"],
            "observed": report["roc_auc"],
            "passed": report["roc_auc"] >= acceptance["roc_auc_min"],
        },
        "f1_min": {
            "threshold": acceptance["f1_min"],
            "observed": report["f1"],
            "passed": report["f1"] >= acceptance["f1_min"],
        },
    }
    if acceptance.get("must_beat_majority_accuracy"):
        checks["beats_majority_accuracy"] = {
            "threshold": report["majority_baseline_accuracy"],
            "observed": report["accuracy"],
            "passed": report["beats_majority_baseline"],
        }

    result = {
        "endpoint_name": endpoint,
        "region": cfg.region,
        "samples": report["samples"],
        "batch_size": args.batch_size,
        "requests_sent": batches,
        "first_observation_id": ids[0],
        "last_observation_id": ids[-1],
        "metrics": report,
        "acceptance": {"checks": checks, "passed": all(c["passed"] for c in checks.values())},
        "sklearn_cross_check": cross_check_with_sklearn(labels, scores, report),
    }
    result["passed"] = result["acceptance"]["passed"] and result["sklearn_cross_check"].get(
        "agrees", True
    )

    out = evidence_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "evaluation.md").write_text(to_markdown(result), encoding="utf-8")

    log(f"[evaluate] acurácia da baseline majoritária {report['majority_baseline_accuracy']:.4f}")
    log(f"[evaluate] acurácia {report['accuracy']:.4f} (ganho {report['accuracy_lift_over_baseline']:+.4f})")
    log(f"[evaluate] precisão {report['precision']:.4f} recall {report['recall']:.4f} f1 {report['f1']:.4f}")
    log(f"[evaluate] roc_auc {report['roc_auc']:.4f} pr_auc {report['pr_auc']:.4f}")
    for name, check in checks.items():
        log(f"  [{'PASS' if check['passed'] else 'FAIL'}] {name}: {check['observed']} vs {check['threshold']}")
    if result["sklearn_cross_check"].get("available"):
        log(f"  [{'PASS' if result['sklearn_cross_check']['agrees'] else 'FAIL'}] métricas concordam com o scikit-learn")
    log(f"[evaluate] escrevi {out / 'evaluation.json'} e evaluation.md")

    emit(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
