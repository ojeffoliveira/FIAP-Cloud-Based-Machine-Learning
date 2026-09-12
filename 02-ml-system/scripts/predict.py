#!/usr/bin/env python3
"""Inferência determinística de verificação (smoke) contra o endpoint publicado.

O payload é fixo de propósito: dois registros que o processo gerador dos dados
coloca em extremos opostos de risco. Um sistema saudável precisa pontuar o
registro de alto risco acima do de baixo risco, o que pega um vetor de features
silenciosamente fora de ordem - o tipo de bug que deixa a acurácia com boa
aparência enquanto toda predição está errada.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab1 import aws_helpers as aws
from lab1.config import emit, evidence_dir, load_config, log
from lab1.data_contract import SMOKE_RECORDS, smoke_payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--endpoint", help="por padrão, o output endpoint_name do Terraform")
    args = parser.parse_args()

    cfg = load_config()
    try:
        session = aws.make_session(cfg.region, args.profile)
        endpoint = args.endpoint or aws.require_output(aws.terraform_outputs(), "endpoint_name")

        status = aws.describe_endpoint(session, endpoint)["EndpointStatus"]
        if status != "InService":
            raise aws.AwsError(f"o endpoint {endpoint} está {status}, não InService")

        names, body = smoke_payload(cfg)
        log(f"[predict] endpoint {endpoint} ({status})")
        log(f"[predict] ordem das features: {', '.join(cfg.feature_order)}")
        for name, line in zip(names, body.split("\n")):
            log(f"[predict] requisição {name}: {line}")

        probabilities = aws.invoke_endpoint_csv(session, endpoint, body)
    except aws.AwsError as exc:
        log(f"[FAIL] {exc}")
        emit({"passed": False, "error": str(exc)})
        return 1

    checks: dict[str, bool] = {}
    for name, probability in zip(names, probabilities):
        log(f"[predict] resposta {name}: p(churn)={probability:.6f}")
    checks["all_finite"] = all(math.isfinite(p) for p in probabilities)
    checks["all_in_unit_interval"] = all(0.0 <= p <= 1.0 for p in probabilities)
    checks["one_probability_per_row"] = len(probabilities) == len(SMOKE_RECORDS)

    by_name = dict(zip(names, probabilities))
    # Sanidade de direção, não afirmação sobre calibração: o gerador faz o
    # high_risk estritamente mais arriscado, então um modelo que inverte os dois
    # está ligado errado.
    checks["high_risk_scored_above_low_risk"] = by_name["high_risk"] > by_name["low_risk"]

    result = {
        "endpoint_name": endpoint,
        "content_type": "text/csv",
        "feature_order": cfg.feature_order,
        "requests": [
            {"name": record["name"], "features": record["features"], "csv": line}
            for record, line in zip(SMOKE_RECORDS, body.split("\n"))
        ],
        "probabilities": {name: round(p, 6) for name, p in by_name.items()},
        "checks": checks,
        "passed": all(checks.values()),
    }

    out = evidence_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "smoke_prediction.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    for name, passed in checks.items():
        log(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    log(f"[{'PASS' if result['passed'] else 'FAIL'}] inferência de smoke")

    emit(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
