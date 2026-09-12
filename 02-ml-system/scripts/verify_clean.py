#!/usr/bin/env python3
"""Prova que o lab não deixou nada cobrando.

O state do Terraform desaparece depois do `make destroy`, então este script não
confia nele: varre SageMaker e S3 pelo prefixo do projeto e afirma que nenhum
endpoint, endpoint configuration, model ou bucket do lab sobreviveu.

Um training job concluído fica no histórico do SageMaker para sempre. Isso é
registro, não recurso em execução, então ele é reportado e explicitamente não
contado como falha de limpeza.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab1 import aws_helpers as aws
from lab1.config import emit, evidence_dir, load_config, log


def bucket_exists(session, bucket: str) -> bool:
    try:
        aws.client(session, "s3").head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchBucket", "NotFound"}:
            return False
        if code == "403":
            # Outra pessoa é dona de um bucket com esse nome; não é nosso e não
            # cobra desta conta, mas dizer isso é melhor que declarar sucesso.
            log(f"[aviso] head_bucket em {bucket} devolveu 403 - o nome existe fora desta conta")
            return False
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    args = parser.parse_args()

    cfg = load_config()
    prefix = cfg.bucket_prefix

    try:
        session = aws.make_session(cfg.region, args.profile)
        identity = aws.whoami(session)
        sagemaker = aws.client(session, "sagemaker")

        endpoints = sagemaker.list_endpoints(NameContains=prefix, MaxResults=100)["Endpoints"]
        configs = sagemaker.list_endpoint_configs(NameContains=prefix, MaxResults=100)[
            "EndpointConfigs"
        ]
        models = sagemaker.list_models(NameContains=prefix, MaxResults=100)["Models"]
        jobs = sagemaker.list_training_jobs(NameContains=prefix, MaxResults=100)[
            "TrainingJobSummaries"
        ]
        bucket = cfg.bucket_name(identity["account_id"])
        bucket_present = bucket_exists(session, bucket)
    except aws.AwsError as exc:
        log(f"[FAIL] {exc}")
        emit({"passed": False, "error": str(exc)})
        return 1

    checks = {
        "no_endpoint": {
            "passed": not endpoints,
            "detail": [e["EndpointName"] for e in endpoints] or f"nenhum endpoint com {prefix!r}",
        },
        "no_endpoint_configuration": {
            "passed": not configs,
            "detail": [c["EndpointConfigName"] for c in configs] or f"nenhuma endpoint config com {prefix!r}",
        },
        "no_sagemaker_model": {
            "passed": not models,
            "detail": [m["ModelName"] for m in models] or f"nenhum model com {prefix!r}",
        },
        "no_lab_bucket": {
            "passed": not bucket_present,
            "detail": f"{bucket} ainda existe" if bucket_present else f"{bucket} não existe mais",
        },
    }

    result = {
        "region": cfg.region,
        "account_id": identity["account_id"],
        "name_prefix": prefix,
        "checks": checks,
        "training_jobs_in_history": [
            {"name": j["TrainingJobName"], "status": j["TrainingJobStatus"]} for j in jobs
        ],
        "training_history_is_not_billable": True,
        "passed": all(c["passed"] for c in checks.values()),
    }

    out = evidence_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "verify_clean.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )

    for name, check in checks.items():
        log(f"  [{'PASS' if check['passed'] else 'FAIL'}] {name}: {check['detail']}")
    if result["training_jobs_in_history"]:
        log(
            f"[info] {len(result['training_jobs_in_history'])} training job(s) permanecem no histórico "
            "do SageMaker - registro, não recurso em execução, e não cobra"
        )
    log(f"[{'PASS' if result['passed'] else 'FAIL'}] não sobrou recurso de serving cobrando")

    emit(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
