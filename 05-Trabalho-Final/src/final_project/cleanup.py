"""`verify-clean` — prova, resource a resource, via chamada direta à API da
AWS, que nada cobrado sobrou depois do `make finish` chamar `destroy`.

Regra que este módulo nunca quebra: **nunca lê `terraform show`/state para
decidir se algo ainda existe.** O Terraform sabe o que ele tentou destruir;
não sabe o que ficou órfão porque o destroy falhou pela metade, nem sobre
objetos que nunca entraram no state (saída do Async Inference, incidentes da
Lambda, artefato de treino). Só a API responde isso — ver
`/tmp/tf-final/shared/ACADEMY.md`: "O verify-clean não olha o estado. Ele
pergunta à AWS."

Cada recurso do trabalho final carrega um sufixo aleatório por ciclo de vida
(`random_id.lifecycle`, ver `terraform/locals.tf`) que este módulo nunca viu
e não pode adivinhar — por isso toda consulta filtra por **prefixo**
(`local.prefix = fiap-final-<student_id>`), nunca por nome exato. A única
exceção é o dashboard, que é criado sem sufixo (nome estável) e por isso é
consultado por nome exato.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

from .aws import AwsError, check_credentials, client, log
from .config import Config


class Checks:
    """Mesmo coletor usado em `evidence.py` — duplicado de propósito: este
    módulo só pode depender de `config.py`/`aws.py` (onda 0, estáveis), nunca
    de outro arquivo que este mesmo agente entrega em paralelo."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.items.append({"check": name, "passed": passed, "detail": detail})
        log(f"{'[OK]' if passed else '[FALHA]'} {name}: {detail}")

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [item for item in self.items if not item["passed"]]

    def summary(self, title: str) -> dict[str, Any]:
        ok = not self.failed
        log("")
        log(f"{'[OK]' if ok else '[FALHA]'} {title}: {len(self.items) - len(self.failed)}/{len(self.items)} verificações passaram")
        return {"passed": ok, "checks_total": len(self.items), "checks_failed": len(self.failed), "checks": self.items}


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# Um checador por família de recurso — cada um devolve (ok, detalhe).
# --------------------------------------------------------------------------- #


def _check_endpoints(sm: Any, prefix: str) -> tuple[bool, str]:
    names = [item["EndpointName"] for item in sm.list_endpoints(NameContains=prefix).get("Endpoints", [])]
    return not names, "nenhum endpoint" if not names else f"{len(names)} endpoint(s) ainda de pé: {names}"


def _check_endpoint_configs(sm: Any, prefix: str) -> tuple[bool, str]:
    names = [item["EndpointConfigName"] for item in sm.list_endpoint_configs(NameContains=prefix).get("EndpointConfigs", [])]
    return not names, "nenhuma endpoint config" if not names else f"{len(names)} endpoint config(s) ainda existem: {names}"


def _check_models(sm: Any, prefix: str) -> tuple[bool, str]:
    names = [item["ModelName"] for item in sm.list_models(NameContains=prefix).get("Models", [])]
    return not names, "nenhum model" if not names else f"{len(names)} model(s) ainda existem: {names}"


def _check_scalable_targets(aas: Any, prefix: str) -> tuple[bool, str]:
    targets = aas.describe_scalable_targets(ServiceNamespace="sagemaker").get("ScalableTargets", [])
    orphans = [t["ResourceId"] for t in targets if prefix in t.get("ResourceId", "")]
    return not orphans, "nenhum scalable target" if not orphans else f"{len(orphans)} scalable target(s) ainda registrados: {orphans}"


def _check_scaling_policies(aas: Any, prefix: str) -> tuple[bool, str]:
    policies = aas.describe_scaling_policies(ServiceNamespace="sagemaker").get("ScalingPolicies", [])
    orphans = [p["PolicyName"] for p in policies if prefix in p.get("PolicyName", "") or prefix in p.get("ResourceId", "")]
    return not orphans, "nenhuma scaling policy" if not orphans else f"{len(orphans)} scaling policy(ies) ainda registradas: {orphans}"


def _check_alarms(cw: Any, prefix: str) -> tuple[bool, str]:
    names = [item["AlarmName"] for item in cw.describe_alarms(AlarmNamePrefix=prefix).get("MetricAlarms", [])]
    return not names, "nenhum alarme" if not names else f"{len(names)} alarme(s) ainda existem: {names}"


def _check_event_rule(events_client: Any, prefix: str) -> tuple[bool, str]:
    names = [item["Name"] for item in events_client.list_rules(NamePrefix=prefix).get("Rules", [])]
    return not names, "nenhuma regra do EventBridge" if not names else f"{len(names)} regra(s) ainda existem: {names}"


def _check_lambda(lambda_client: Any, prefix: str) -> tuple[bool, str]:
    """`list_functions` não filtra por prefixo do lado do servidor — a lista
    de funções da conta Academy é pequena o bastante para paginar e filtrar
    localmente sem custo relevante."""
    matches: list[str] = []
    paginator = lambda_client.get_paginator("list_functions")
    for page in paginator.paginate():
        for fn in page.get("Functions", []):
            if fn["FunctionName"].startswith(prefix):
                matches.append(fn["FunctionName"])
    return not matches, "nenhuma function Lambda" if not matches else f"{len(matches)} function(s) ainda existem: {matches}"


def _check_dashboard(cw: Any, dashboard_name: str) -> tuple[bool, str]:
    try:
        cw.get_dashboard(DashboardName=dashboard_name)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFound":
            return True, f"dashboard {dashboard_name!r} não existe"
        raise
    return False, f"dashboard {dashboard_name!r} ainda existe"


def _check_bucket(s3: Any, bucket_name: str) -> tuple[bool, str]:
    """404 prova ausência. 403 significa "existe mas não vejo o conteúdo" —
    nunca tratado como PASS: um bucket que responde 403 pode ainda estar
    cobrando armazenamento, e o próprio 03-serving-and-scaling/scripts de
    referência trata os dois códigos como fatos distintos."""
    try:
        s3.head_bucket(Bucket=bucket_name)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code") or str(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404 or code in ("404", "NoSuchBucket"):
            return True, f"bucket {bucket_name!r} não existe (404)"
        if status == 403:
            return False, f"bucket {bucket_name!r} respondeu 403 (existe, sem acesso ao conteúdo daqui) — precisa de verificação manual"
        return False, f"bucket {bucket_name!r}: erro inesperado ao consultar ({exc})"
    return False, f"bucket {bucket_name!r} ainda existe"


def _check_training_jobs_in_progress(sm: Any, prefix: str) -> tuple[bool, str]:
    names = [
        item["TrainingJobName"]
        for item in sm.list_training_jobs(StatusEquals="InProgress", NameContains=prefix).get("TrainingJobSummaries", [])
    ]
    return not names, "nenhum training job em andamento" if not names else f"{len(names)} training job(s) em andamento: {names}"


def _check_transform_jobs_in_progress(sm: Any, prefix: str) -> tuple[bool, str]:
    names = [
        item["TransformJobName"]
        for item in sm.list_transform_jobs(StatusEquals="InProgress", NameContains=prefix).get("TransformJobSummaries", [])
    ]
    return not names, "nenhum transform job em andamento" if not names else f"{len(names)} transform job(s) em andamento: {names}"


def cmd_verify_clean(cfg: Config, args: Any) -> int:
    """Consulta a API, resource a resource, e grava `artifacts/evidence/cleanup.json`
    com o que foi perguntado, quando, e o resultado. Saída não-zero se
    qualquer coisa cobrável ainda existir."""
    try:
        identity = check_credentials(cfg.region)
    except AwsError as exc:
        log(f"[FALHA] verify-clean não pôde nem confirmar a credencial: {exc}")
        resultado = {
            "passed": False,
            "checks_total": 0,
            "checks_failed": 1,
            "checks": [{"check": "credenciais_aws", "passed": False, "detail": str(exc)}],
        }
        _write_cleanup_json(cfg, resultado)
        _emit(resultado)
        return 1

    region = cfg.region
    prefix = cfg.prefix
    bucket_name = f"{prefix}-{identity['account']}"
    dashboard_name = f"{prefix}-dashboard"

    sm = client("sagemaker", region)
    aas = client("application-autoscaling", region)
    cw = client("cloudwatch", region)
    events_client = client("events", region)
    lambda_client = client("lambda", region)
    s3 = client("s3", region)

    checks = Checks()

    named_checks: tuple[tuple[str, tuple[bool, str]], ...] = (
        ("endpoints", _check_endpoints(sm, prefix)),
        ("endpoint_configs", _check_endpoint_configs(sm, prefix)),
        ("models", _check_models(sm, prefix)),
        ("scalable_targets", _check_scalable_targets(aas, prefix)),
        ("scaling_policies", _check_scaling_policies(aas, prefix)),
        ("alarms", _check_alarms(cw, prefix)),
        ("eventbridge_rule", _check_event_rule(events_client, prefix)),
        ("lambda_function", _check_lambda(lambda_client, prefix)),
        ("dashboard", _check_dashboard(cw, dashboard_name)),
        ("bucket", _check_bucket(s3, bucket_name)),
        ("training_jobs_em_andamento", _check_training_jobs_in_progress(sm, prefix)),
        ("transform_jobs_em_andamento", _check_transform_jobs_in_progress(sm, prefix)),
    )
    for name, (ok, detail) in named_checks:
        checks.add(name, ok, detail)

    resultado = checks.summary("verify-clean")
    resultado["region"] = region
    resultado["prefix"] = prefix
    resultado["account"] = identity["account"]
    _write_cleanup_json(cfg, resultado)
    _emit(resultado)
    return 0 if resultado["passed"] else 1


def _write_cleanup_json(cfg: Config, resultado: dict[str, Any]) -> None:
    payload = {
        "schema_version": "1.0.0",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "region": resultado.get("region", cfg.region),
        "prefix": resultado.get("prefix", cfg.prefix),
        "account": resultado.get("account", ""),
        "passed": resultado["passed"],
        "checks_total": resultado["checks_total"],
        "checks_failed": resultado["checks_failed"],
        "checks": resultado["checks"],
    }
    path = cfg.evidence_dir / "cleanup.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"[verify-clean] gravado {path}")


if __name__ == "__main__":
    from .config import load_config

    sys.exit(cmd_verify_clean(load_config(), None))
