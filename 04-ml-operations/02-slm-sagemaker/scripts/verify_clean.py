#!/usr/bin/env python3
"""Prova por API que nada do Lab 04.2 continua cobrando — nunca consulta o state.

Regra dura do DNA do repositório: o Terraform state pode estar corrompido, o
`destroy` pode ter falhado no meio, ou um recurso pode ter nascido fora do
Terraform. A única prova aceitável é perguntar a cada serviço, reconstruindo o
prefixo a partir de `config/lab.yaml` — nunca dos outputs de um state que pode
não existir.

Uso: python scripts/verify_clean.py
Saída: stdout = JSON com contagem por categoria e veredito; stderr = progresso.
Exit code: 0 só se PASS (zero recurso faturável do lab).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab42 import aws, evidence
from lab42.aws import LabError, emit, log

PASS, FAIL = "[PASS]", "[FAIL]"


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(
        self,
        name: str,
        passed: bool,
        detail: str,
        *,
        comando_remocao: str | None = None,
    ) -> None:
        item: dict[str, Any] = {"check": name, "passed": passed, "detail": detail}
        if comando_remocao and not passed:
            item["comando_remocao"] = comando_remocao
        self.items.append(item)
        marker = PASS if passed else FAIL
        log(f"{marker} {name}: {detail}")
        if comando_remocao and not passed:
            log(f"    remoção manual: {comando_remocao}")

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [item for item in self.items if not item["passed"]]

    def summary(self, title: str) -> dict[str, Any]:
        ok = not self.failed
        log("")
        log(
            f"{PASS if ok else FAIL} {title}: "
            f"{len(self.items) - len(self.failed)}/{len(self.items)} verificações passaram"
        )
        return {
            "veredito": "PASS" if ok else "FAIL",
            "checks_total": len(self.items),
            "checks_failed": len(self.failed),
            "checks": self.items,
        }


def main() -> int:
    cfg = aws.load_config()
    prefixo = cfg["aws"]["bucket_prefix"]
    dashboard_prefixo = "fiap-mlops-"
    checks = Checks()

    region = aws.region()
    sagemaker = aws.client("sagemaker")
    autoscaling = aws.client("application-autoscaling")
    ec2 = aws.client("ec2")
    s3 = aws.client("s3")
    cloudwatch = aws.client("cloudwatch")

    # --- SageMaker: endpoints, configs, models ------------------------------
    endpoints = [
        e["EndpointName"]
        for e in sagemaker.list_endpoints(NameContains=prefixo, MaxResults=100).get(
            "Endpoints", []
        )
    ]
    checks.add(
        "no_endpoint",
        not endpoints,
        "nenhum endpoint com o prefixo do lab"
        if not endpoints
        else f"AINDA COBRANDO: {', '.join(endpoints)}",
        comando_remocao=None
        if not endpoints
        else " ; ".join(
            f"aws sagemaker delete-endpoint --endpoint-name {n} --region {region}"
            for n in endpoints
        ),
    )

    configs = [
        c["EndpointConfigName"]
        for c in sagemaker.list_endpoint_configs(
            NameContains=prefixo, MaxResults=100
        ).get("EndpointConfigs", [])
    ]
    checks.add(
        "no_endpoint_config",
        not configs,
        "nenhum endpoint config" if not configs else f"restaram: {', '.join(configs)}",
        comando_remocao=None
        if not configs
        else " ; ".join(
            f"aws sagemaker delete-endpoint-config --endpoint-config-name {n} --region {region}"
            for n in configs
        ),
    )

    modelos = [
        m["ModelName"]
        for m in sagemaker.list_models(NameContains=prefixo, MaxResults=100).get(
            "Models", []
        )
    ]
    checks.add(
        "no_model",
        not modelos,
        "nenhum model" if not modelos else f"restaram: {', '.join(modelos)}",
        comando_remocao=None
        if not modelos
        else " ; ".join(
            f"aws sagemaker delete-model --model-name {n} --region {region}"
            for n in modelos
        ),
    )

    # --- Application Auto Scaling (namespace sagemaker) ---------------------
    targets = [
        t["ResourceId"]
        for t in autoscaling.describe_scalable_targets(
            ServiceNamespace="sagemaker"
        ).get("ScalableTargets", [])
        if prefixo in t["ResourceId"]
    ]
    checks.add(
        "no_scalable_target",
        not targets,
        "nenhum scalable target do lab"
        if not targets
        else f"restaram: {', '.join(targets)}",
        comando_remocao=None
        if not targets
        else " ; ".join(
            "aws application-autoscaling deregister-scalable-target --service-namespace sagemaker "
            f"--resource-id {rid} --scalable-dimension sagemaker:variant:DesiredInstanceCount --region {region}"
            for rid in targets
        ),
    )

    policies = [
        p["PolicyName"]
        for p in autoscaling.describe_scaling_policies(
            ServiceNamespace="sagemaker"
        ).get("ScalingPolicies", [])
        if prefixo in p["ResourceId"]
    ]
    checks.add(
        "no_scaling_policy",
        not policies,
        "nenhuma scaling policy do lab"
        if not policies
        else f"restaram: {', '.join(policies)}",
    )

    # --- EC2 por tag do lab (runner) -----------------------------------------
    reservas = ec2.describe_instances(
        Filters=[
            {
                "Name": "tag:lab",
                "Values": [str(cfg.get("tags", {}).get("lab", "04-2-slm-sagemaker"))],
            },
            {
                "Name": "instance-state-name",
                "Values": ["pending", "running", "stopping", "stopped"],
            },
        ]
    ).get("Reservations", [])
    instancias = [i["InstanceId"] for r in reservas for i in r["Instances"]]
    checks.add(
        "no_ec2_runner",
        not instancias,
        "nenhuma instância EC2 do lab"
        if not instancias
        else f"AINDA COBRANDO: {', '.join(instancias)}",
        comando_remocao=None
        if not instancias
        else f"aws ec2 terminate-instances --instance-ids {' '.join(instancias)} --region {region}",
    )

    # --- S3: bucket de state e de artifacts, com seus prefixos ---------------
    buckets_do_lab = [
        b["Name"]
        for b in s3.list_buckets().get("Buckets", [])
        if b["Name"].startswith(prefixo)
    ]
    checks.add(
        "no_bucket",
        not buckets_do_lab,
        "nenhum bucket do lab"
        if not buckets_do_lab
        else f"restaram: {', '.join(buckets_do_lab)}",
        comando_remocao=None
        if not buckets_do_lab
        else " ; ".join(f"aws s3 rb s3://{b} --force" for b in buckets_do_lab),
    )

    # --- CloudWatch dashboards -------------------------------------------------
    # O dashboard não aparece em nenhuma listagem de compute e continua
    # existindo (e custando, embora pouco) depois do endpoint morrer — mesmo
    # achado documentado no 04.1.
    paineis = [
        d["DashboardName"]
        for d in cloudwatch.list_dashboards(DashboardNamePrefix=dashboard_prefixo).get(
            "DashboardEntries", []
        )
    ]
    checks.add(
        "no_dashboard",
        not paineis,
        f"nenhum dashboard {dashboard_prefixo}*"
        if not paineis
        else f"restaram: {', '.join(paineis)}",
        comando_remocao=None
        if not paineis
        else " ; ".join(
            f"aws cloudwatch delete-dashboards --dashboard-names {d}" for d in paineis
        ),
    )

    resultado = checks.summary("verify-clean")
    resultado["prefixo"] = prefixo
    resultado["regiao"] = region

    # cleanup.json é a evidência persistida em disco (spec §14); o stdout acima
    # já é o resultado que quem chamou `make verify-clean` vai capturar — aqui
    # só grava o mesmo veredito num arquivo que sobrevive ao término do processo.
    checked_at = datetime.now(timezone.utc).isoformat()
    evidence.write(
        "cleanup.json",
        {
            "verify_clean_passed": resultado["veredito"] == "PASS",
            "resources_found": [item["detail"] for item in checks.failed],
            "checked_at": checked_at,
            "prefixo": prefixo,
            "regiao": region,
        },
    )

    emit(resultado)
    return 0 if resultado["veredito"] == "PASS" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LabError as exc:
        log(f"[FAIL] {exc}")
        sys.exit(1)
