#!/usr/bin/env python3
"""Superfície única de CLI do Lab 03 - Serving and Scaling.

Todo subcomando escreve o resultado estruturado em stdout (JSON) e a narração em
stderr, conforme a convenção do lab. Os subcomandos mapeiam 1:1 para os alvos do
Makefile: doctor, data, validate-data, wait-training (interno ao apply), status,
compare, async, batch, load, scale-demo, evidence, verify-clean.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fiap_serving_scaling import aws, data as datamod, metrics
from fiap_serving_scaling.config import (
    ASYNC_PAYLOAD_FILE,
    BATCH_INPUT_FILE,
    DATA_DIR,
    EVIDENCE_DIR,
    TERRAFORM_DIR,
    TEST_FEATURES_FILE,
    emit,
    load_config,
    log,
)
from fiap_serving_scaling.evidence import build_resources_snapshot, build_summary, write_evidence
from fiap_serving_scaling.serving import read_csv_rows, rows_to_body

PROJECT_PREFIX = "prb-cloud-ml-lab2"


def _session(args: argparse.Namespace, region: str) -> boto3.session.Session:
    return aws.make_session(region, args.profile)


def _write_result(name: str, result: dict) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVIDENCE_DIR / name, "w", encoding="utf-8") as handle:
        json.dump(aws.json_safe(result), handle, indent=2, sort_keys=True)


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = load_config()
    result: dict = {"region_required": cfg.region, "checks": {}}

    try:
        session = _session(args, cfg.region)
        identity = aws.whoami(session)
        role_arn = aws.resolve_lab_role(session, cfg.execution_role_name)
    except aws.AwsError as exc:
        log(f"[FAIL] {exc}")
        result["passed"] = False
        result["error"] = str(exc)
        emit(result)
        return 1

    readonly_checks: dict[str, bool] = {}
    try:
        aws.client(session, "sagemaker").list_endpoints(MaxResults=1)
        readonly_checks["sagemaker_reachable"] = True
    except Exception as exc:  # noqa: BLE001
        readonly_checks["sagemaker_reachable"] = False
        log(f"[aviso] a verificação somente-leitura do SageMaker falhou: {exc}")

    try:
        aws.client(session, "s3").list_buckets()
        readonly_checks["s3_reachable"] = True
    except Exception as exc:  # noqa: BLE001
        readonly_checks["s3_reachable"] = False
        log(f"[aviso] a verificação somente-leitura do S3 falhou: {exc}")

    try:
        aws.client(session, "cloudwatch").describe_alarms(MaxRecords=1)
        readonly_checks["cloudwatch_reachable"] = True
    except Exception as exc:  # noqa: BLE001
        readonly_checks["cloudwatch_reachable"] = False
        log(f"[aviso] a verificação somente-leitura do CloudWatch falhou: {exc}")

    try:
        aws.client(session, "application-autoscaling").describe_scalable_targets(
            ServiceNamespace="sagemaker"
        )
        readonly_checks["application_autoscaling_reachable"] = True
    except Exception as exc:  # noqa: BLE001
        readonly_checks["application_autoscaling_reachable"] = False
        log(f"[aviso] a verificação somente-leitura do Application Auto Scaling falhou: {exc}")

    result["checks"] = {
        "credentials_usable": True,
        "region_is_required_region": session.region_name == cfg.region,
        "execution_role_resolved": bool(role_arn),
        **readonly_checks,
    }
    result["account_id"] = identity["account_id"]
    result["caller_arn"] = identity["arn"]
    result["execution_role_arn"] = role_arn
    result["bucket_name"] = cfg.bucket_name(identity["account_id"])
    result["passed"] = all(
        result["checks"][k] for k in ("credentials_usable", "region_is_required_region", "execution_role_resolved")
    )

    log("Verificação prévia da AWS")
    log(f"  conta            : {identity['account_id']}")
    log(f"  chamador         : {aws.mask_arn(identity['arn'])}")
    log(f"  região           : {session.region_name} (exigida {cfg.region})")
    log(f"  role de execução : {aws.mask_arn(role_arn)}")
    log(f"  bucket do lab    : {result['bucket_name']}")
    for key, value in readonly_checks.items():
        log(f"  {key:<32}: {'ok' if value else 'FALHOU (não fatal)'}")
    log("  este lab nunca imprime credencial")
    log(f"[{'PASS' if result['passed'] else 'FAIL'}] verificação prévia")

    emit(result)
    return 0 if result["passed"] else 1


# --------------------------------------------------------------------------- #
# data / validate-data
# --------------------------------------------------------------------------- #


def cmd_data(_args: argparse.Namespace) -> int:
    cfg = load_config()
    manifest = datamod.generate(cfg, DATA_DIR)
    emit(manifest)
    return 0


def cmd_validate_data(_args: argparse.Namespace) -> int:
    cfg = load_config()
    result = datamod.validate(cfg, DATA_DIR)
    for name, passed in result["checks"].items():
        log(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    log(f"[{'PASS' if result['passed'] else 'FAIL'}] contrato de dados")
    emit(result)
    return 0 if result["passed"] else 1


# --------------------------------------------------------------------------- #
# wait-training (etapa interna do `make apply`)
# --------------------------------------------------------------------------- #


def cmd_wait_training(args: argparse.Namespace) -> int:
    cfg = load_config()
    outputs = aws.terraform_outputs()
    job_name = aws.require_output(outputs, "training_job_name")
    session = _session(args, cfg.region)

    description = aws.wait_training_job(session, job_name, timeout_seconds=1200)
    status = description["TrainingJobStatus"]
    if status != "Completed":
        raise aws.AwsError(f"o training job {job_name} terminou como {status}, não Completed")

    artifact_uri = description["ModelArtifacts"]["S3ModelArtifacts"]
    bucket, key = aws.split_s3_uri(artifact_uri)
    head = aws.poll_for_object(session, bucket, key, poll_seconds=5, timeout_seconds=120)

    handoff = {"deploy_serving": True, "model_artifact_uri": artifact_uri}
    handoff_path = TERRAFORM_DIR / "artifact.auto.tfvars.json"
    with open(handoff_path, "w", encoding="utf-8") as handle:
        json.dump(handoff, handle, indent=2)

    billable = description.get("BillableTimeInSeconds")
    log(f"[wait] {job_name}: Completed em {billable}s cobrados")
    log(f"[wait] artefato comprovado via HeadObject: s3://{bucket}/{key} ({head['content_length']} bytes)")
    log(f"[wait] passagem de bastão escrita em {handoff_path}")

    emit(
        {
            "training_job_name": job_name,
            "status": status,
            "billable_seconds": billable,
            "artifact_uri": artifact_uri,
            "artifact_size_bytes": head["content_length"],
            "artifact_etag_present": bool(head["etag"]),
        }
    )
    return 0


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config()
    outputs = aws.terraform_outputs()
    session = _session(args, cfg.region)

    result: dict = {"endpoints": {}, "scaling": {}}
    for mode, name_key in (
        ("realtime", "realtime_endpoint_name"),
        ("serverless", "serverless_endpoint_name"),
        ("async", "async_endpoint_name"),
    ):
        name = outputs.get(name_key)
        if not name:
            result["endpoints"][mode] = {"exists": False}
            continue
        description = aws.describe_endpoint(session, name)
        variants = description.get("ProductionVariants", [])
        result["endpoints"][mode] = {
            "exists": True,
            "name": name,
            "status": description["EndpointStatus"],
            "current_instance_count": variants[0].get("CurrentInstanceCount") if variants else None,
        }
        log(f"[status] {mode:<10} {name}: {description['EndpointStatus']}")

    for mode, resource_id_key in (
        ("realtime", "realtime_scalable_resource_id"),
        ("async", "async_scalable_resource_id"),
    ):
        resource_id = outputs.get(resource_id_key)
        if not resource_id:
            continue
        targets = aws.describe_scalable_targets(session, resource_id)
        policies = aws.describe_scaling_policies(session, resource_id)
        result["scaling"][mode] = {
            "resource_id": resource_id,
            "min_capacity": targets[0]["MinCapacity"] if targets else None,
            "max_capacity": targets[0]["MaxCapacity"] if targets else None,
            "policy_names": [p["PolicyName"] for p in policies],
        }
        log(f"[status] {mode:<10} escala: min={result['scaling'][mode]['min_capacity']} max={result['scaling'][mode]['max_capacity']} policies={len(policies)}")

    emit(result)
    return 0


# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #


def cmd_dashboard(args: argparse.Namespace) -> int:
    cfg = load_config()
    outputs = aws.terraform_outputs()
    nome = outputs.get("dashboard_name") or ""
    url = outputs.get("dashboard_url") or ""
    if not nome:
        raise aws.AwsError("o painel só existe depois do `make apply` (estágio 2).")

    # GetDashboard de verdade: imprimir um link que devolve 404 é pior que não
    # imprimir nada, e a contagem de widgets confirma que o corpo subiu inteiro.
    session = _session(args, cfg.region)
    corpo = json.loads(aws.get_dashboard(session, nome)["DashboardBody"])
    widgets = len(corpo.get("widgets", []))

    log("")
    log(f"  Painel  : {nome}")
    log(f"  Widgets : {widgets}")
    log("")
    log("  Abra o link abaixo e DEIXE ABERTO durante o lab inteiro. Ele atualiza")
    log("  sozinho conforme novas métricas chegam (granularidade de 60 s).")
    log("")
    print(url)
    return 0


# --------------------------------------------------------------------------- #
# compare (real-time vs serverless)
# --------------------------------------------------------------------------- #


def cmd_compare(args: argparse.Namespace) -> int:
    cfg = load_config()
    outputs = aws.terraform_outputs()
    session = _session(args, cfg.region)

    rows = read_csv_rows(DATA_DIR / TEST_FEATURES_FILE)[:5]
    body = rows_to_body(rows)

    result: dict = {}
    for mode, name_key in (("realtime", "realtime_endpoint_name"), ("serverless", "serverless_endpoint_name")):
        endpoint_name = aws.require_output(outputs, name_key)
        first_probs, first_elapsed = aws.invoke_endpoint_csv(session, endpoint_name, body)
        warm_latencies = []
        warm_probs = first_probs
        for _ in range(20):
            probs, elapsed = aws.invoke_endpoint_csv(session, endpoint_name, body)
            warm_latencies.append(elapsed * 1000.0)
            warm_probs = probs
        stats = metrics.latency_stats(warm_latencies)
        result[mode] = {
            "first_ms": round(first_elapsed * 1000.0, 3),
            "warm_p50_ms": stats["p50_ms"],
            "warm_p95_ms": stats["p95_ms"],
            "success_rate": 1.0,
            "sample_predictions": warm_probs,
        }
        log(f"[compare] {mode:<10} first={result[mode]['first_ms']}ms warm_p50={stats['p50_ms']}ms warm_p95={stats['p95_ms']}ms")

    tolerance = cfg.predictions_tolerance
    predictions_match = all(
        abs(a - b) <= tolerance
        for a, b in zip(result["realtime"]["sample_predictions"], result["serverless"]["sample_predictions"], strict=True)
    )
    result["predictions_match"] = predictions_match
    log(f"[compare] predictions_match={predictions_match} (tolerância {tolerance})")

    _write_result("compare.json", result)
    emit(result)
    return 0 if predictions_match else 1


# --------------------------------------------------------------------------- #
# async
# --------------------------------------------------------------------------- #


def cmd_async(args: argparse.Namespace) -> int:
    cfg = load_config()
    outputs = aws.terraform_outputs()
    session = _session(args, cfg.region)

    endpoint_name = aws.require_output(outputs, "async_endpoint_name")
    bucket = aws.require_output(outputs, "bucket_name")

    before = aws.describe_endpoint(session, endpoint_name)
    before_variants = before.get("ProductionVariants", [])
    capacity_before = before_variants[0].get("CurrentInstanceCount") if before_variants else None

    local_path = DATA_DIR / ASYNC_PAYLOAD_FILE
    input_rows = read_csv_rows(local_path)
    timestamp = int(time.time())
    input_key = f"async/input/{timestamp}.csv"
    input_uri = aws.upload_file(session, str(local_path), bucket, input_key)
    log(f"[async] payload de {len(input_rows)} linhas enviado para {input_uri}")

    invocation = aws.invoke_endpoint_async(session, endpoint_name, input_uri)
    log(f"[async] InferenceId={invocation['inference_id']} output={invocation['output_location']}")

    output_bucket, output_key = aws.split_s3_uri(invocation["output_location"])
    aws.poll_for_object(session, output_bucket, output_key, poll_seconds=10, timeout_seconds=600)
    output_text = aws.download_text(session, output_bucket, output_key)
    output_probs = aws.parse_csv_probabilities(output_text)

    after = aws.describe_endpoint(session, endpoint_name)
    after_variants = after.get("ProductionVariants", [])
    capacity_after = after_variants[0].get("CurrentInstanceCount") if after_variants else None

    result = {
        "endpoint_name": endpoint_name,
        "input_uri": input_uri,
        "input_count": len(input_rows),
        "output_uri": invocation["output_location"],
        "output_count": len(output_probs),
        "inference_id": invocation["inference_id"],
        "capacity_before": capacity_before,
        "capacity_after_observation": capacity_after,
    }
    log(f"[async] input_count={result['input_count']} output_count={result['output_count']}")

    _write_result("async.json", result)
    emit(result)
    return 0 if result["output_count"] == result["input_count"] else 1


# --------------------------------------------------------------------------- #
# batch
# --------------------------------------------------------------------------- #


def cmd_batch(args: argparse.Namespace) -> int:
    cfg = load_config()
    outputs = aws.terraform_outputs()
    session = _session(args, cfg.region)

    model_name = aws.require_output(outputs, "model_name")
    bucket = aws.require_output(outputs, "bucket_name")

    local_path = DATA_DIR / BATCH_INPUT_FILE
    input_rows = read_csv_rows(local_path)
    timestamp = int(time.time())
    input_key = f"batch/input/{timestamp}/{BATCH_INPUT_FILE}"
    input_uri = aws.upload_file(session, str(local_path), bucket, input_key)

    output_prefix = f"batch/output/{timestamp}/"
    output_uri = f"s3://{bucket}/{output_prefix}"

    batch_cfg = cfg.batch
    job_name = f"{PROJECT_PREFIX}-batch-{timestamp}"
    log(f"[batch] criando o transform job {job_name}")
    aws.create_transform_job(
        session,
        job_name=job_name,
        model_name=model_name,
        input_s3_uri=input_uri,
        output_s3_uri=output_uri,
        instance_type=batch_cfg["instance_type"],
        max_concurrent_transforms=batch_cfg["max_concurrent_transforms"],
        max_payload_in_mb=batch_cfg["max_payload_in_mb"],
        batch_strategy=batch_cfg["batch_strategy"],
    )

    description = aws.wait_transform_job(session, job_name, timeout_seconds=900)
    status = description["TransformJobStatus"]
    if status != "Completed":
        raise aws.AwsError(f"o transform job {job_name} terminou como {status}")

    # Descobrir o objeto de saída em vez de montar "<arquivo>.out" à mão: o mesmo
    # princípio que a passagem de bastão do artefato de treino já segue.
    output_keys = aws.list_objects(session, bucket, output_prefix)
    if len(output_keys) != 1:
        raise aws.AwsError(
            f"esperava exatamente 1 objeto de saída em s3://{bucket}/{output_prefix}, encontrei {output_keys}"
        )
    output_key = output_keys[0]
    output_text = aws.download_text(session, bucket, output_key)
    output_probs = aws.parse_csv_probabilities(output_text)

    duration_s = None
    if description.get("TransformEndTime") and description.get("TransformStartTime"):
        duration_s = (description["TransformEndTime"] - description["TransformStartTime"]).total_seconds()

    result = {
        "transform_job_name": job_name,
        "status": status,
        "input_uri": input_uri,
        "input_count": len(input_rows),
        "output_uri": f"s3://{bucket}/{output_key}",
        "output_count": len(output_probs),
        "duration_seconds_observed": duration_s,
    }
    log(f"[batch] output_count={result['output_count']} duração_observada={duration_s}s")

    _write_result("batch.json", result)
    emit(result)
    return 0 if result["output_count"] == 600 else 1


# --------------------------------------------------------------------------- #
# load
# --------------------------------------------------------------------------- #


def cmd_load(args: argparse.Namespace) -> int:
    cfg = load_config()
    outputs = aws.terraform_outputs()
    session = _session(args, cfg.region)
    endpoint_name = aws.require_output(outputs, "realtime_endpoint_name")

    rows = read_csv_rows(DATA_DIR / TEST_FEATURES_FILE)

    levels = []
    for level in cfg.load_test_matrix:
        body = rows[0]
        result = metrics.run_load_level(
            session, endpoint_name, body, concurrency=level["concurrency"], requests=level["requests"]
        )
        levels.append(result)
        log(
            f"[load] concurrency={level['concurrency']:<3} requests={level['requests']:<4} "
            f"success_rate={result['success_rate']} p50={result['p50_ms']}ms p95={result['p95_ms']}ms "
            f"rps={result['requests_per_second']}"
        )

    min_rate = cfg.load_test_min_success_rate
    overall_ok = all(level["success_rate"] >= min_rate for level in levels)
    result = {"endpoint_name": endpoint_name, "levels": levels, "min_success_rate_required": min_rate, "passed": overall_ok}

    _write_result("load.json", result)
    emit(result)
    return 0 if overall_ok else 1


# --------------------------------------------------------------------------- #
# scale-demo
# --------------------------------------------------------------------------- #


def cmd_scale_demo(args: argparse.Namespace) -> int:
    cfg = load_config()
    outputs = aws.terraform_outputs()
    session = _session(args, cfg.region)

    endpoint_name = aws.require_output(outputs, "realtime_endpoint_name")
    resource_id = aws.require_output(outputs, "realtime_scalable_resource_id")
    timeout = cfg.scale_demo_wait_timeout_s
    target = cfg.scale_demo_target_min_capacity

    before = aws.describe_endpoint(session, endpoint_name)["ProductionVariants"][0]["CurrentInstanceCount"]
    log(f"[scale] antes: {before}")

    log(f"[scale] subindo MinCapacity/MaxCapacity para {target} para forçar um scale-out determinístico")
    aws.register_scalable_target_min_capacity(session, resource_id, min_capacity=target, max_capacity=target)
    scaled = aws.wait_instance_count(session, endpoint_name, target_count=target, timeout_seconds=timeout)
    aws.wait_endpoint_in_service(session, endpoint_name, timeout_seconds=timeout)
    log(f"[scale] escalado: {scaled}")

    log("[scale] restaurando MinCapacity=1, MaxCapacity=2 (valores gerenciados pelo Terraform, sem deixar drift)")
    aws.register_scalable_target_min_capacity(session, resource_id, min_capacity=1, max_capacity=2)
    log("[scale] forçando DesiredInstanceCount de volta para 1: baixar só o MaxCapacity não faz o "
        "Application Auto Scaling reduzir, isso só acontece quando o alarme de target tracking avalia")
    aws.set_endpoint_desired_capacity(session, endpoint_name, desired_instance_count=1)
    restored = aws.wait_instance_count(session, endpoint_name, target_count=1, timeout_seconds=timeout)
    aws.wait_endpoint_in_service(session, endpoint_name, timeout_seconds=timeout)
    log(f"[scale] restaurado: {restored}")

    result = {"endpoint_name": endpoint_name, "before": before, "scaled": scaled, "restored": restored}
    _write_result("scale.json", result)
    emit(result)
    return 0 if (before == 1 and scaled == target and restored == 1) else 1


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #


def cmd_evidence(args: argparse.Namespace) -> int:
    cfg = load_config()
    outputs = aws.terraform_outputs()
    session = _session(args, cfg.region)

    resources = build_resources_snapshot(session, outputs)
    _write_result("resources.json", resources)

    summary = build_summary(EVIDENCE_DIR, resources)
    write_evidence(EVIDENCE_DIR, summary)

    log(f"[evidence] chain_complete={summary['chain_complete']}")
    emit({"chain_complete": summary["chain_complete"], "checks": summary["checks"], "evidence_dir": str(EVIDENCE_DIR)})
    return 0 if summary["chain_complete"] else 1


# --------------------------------------------------------------------------- #
# verify-clean (trabalha pelas APIs da AWS por prefixo de nome, nunca pelo state)
# --------------------------------------------------------------------------- #


def cmd_verify_clean(args: argparse.Namespace) -> int:
    cfg = load_config()
    session = _session(args, cfg.region)
    prefix = PROJECT_PREFIX

    identity = aws.whoami(session)
    bucket_name = cfg.bucket_name(identity["account_id"])

    sagemaker = aws.client(session, "sagemaker")
    aas = aws.client(session, "application-autoscaling")
    cloudwatch = aws.client(session, "cloudwatch")
    s3 = aws.client(session, "s3")

    checks: dict[str, bool] = {}
    details: dict = {}

    endpoints = sagemaker.list_endpoints(NameContains=prefix)["Endpoints"]
    checks["no_endpoints_for_prefix"] = len(endpoints) == 0
    details["endpoints"] = [e["EndpointName"] for e in endpoints]

    configs = sagemaker.list_endpoint_configs(NameContains=prefix)["EndpointConfigs"]
    checks["no_endpoint_configs_for_prefix"] = len(configs) == 0
    details["endpoint_configs"] = [c["EndpointConfigName"] for c in configs]

    models = sagemaker.list_models(NameContains=prefix)["Models"]
    checks["no_models_for_prefix"] = len(models) == 0
    details["models"] = [m["ModelName"] for m in models]

    all_targets = aas.describe_scalable_targets(ServiceNamespace="sagemaker")["ScalableTargets"]
    prefixed_targets = [t for t in all_targets if prefix in t["ResourceId"]]
    checks["no_scalable_targets_for_prefix"] = len(prefixed_targets) == 0
    details["scalable_targets"] = [t["ResourceId"] for t in prefixed_targets]

    all_policies = aas.describe_scaling_policies(ServiceNamespace="sagemaker")["ScalingPolicies"]
    prefixed_policies = [p for p in all_policies if prefix in p["PolicyName"]]
    checks["no_scaling_policies_for_prefix"] = len(prefixed_policies) == 0
    details["scaling_policies"] = [p["PolicyName"] for p in prefixed_policies]

    alarms = cloudwatch.describe_alarms(AlarmNamePrefix=prefix)["MetricAlarms"]
    checks["no_cloudwatch_alarms_for_prefix"] = len(alarms) == 0
    details["cloudwatch_alarms"] = [a["AlarmName"] for a in alarms]

    # O painel não cobra nada, mas fica visível no console da conta e dá a
    # impressão de que o lab continua no ar. Além disso, painel sobrando é sinal
    # de destroy incompleto — vale como sintoma, não como custo.
    dashboards = aws.dashboard_names(session, prefix)
    checks["no_cloudwatch_dashboards_for_prefix"] = len(dashboards) == 0
    details["cloudwatch_dashboards"] = dashboards

    try:
        s3.head_bucket(Bucket=bucket_name)
        bucket_exists = True
    except Exception:  # noqa: BLE001
        bucket_exists = False
    checks["no_lab_bucket"] = not bucket_exists
    details["bucket_name"] = bucket_name

    active_training = sagemaker.list_training_jobs(NameContains=prefix, StatusEquals="InProgress")["TrainingJobSummaries"]
    active_transform = sagemaker.list_transform_jobs(NameContains=prefix, StatusEquals="InProgress")["TransformJobSummaries"]
    checks["no_active_training_or_transform_jobs"] = len(active_training) == 0 and len(active_transform) == 0
    details["active_training_jobs"] = [j["TrainingJobName"] for j in active_training]
    details["active_transform_jobs"] = [j["TransformJobName"] for j in active_transform]

    for name, passed in checks.items():
        log(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    passed = all(checks.values())
    log(f"[{'PASS' if passed else 'FAIL'}] verificação de limpeza")

    emit({"passed": passed, "checks": checks, "details": details})
    return 0 if passed else 1


# --------------------------------------------------------------------------- #
# entrypoint
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    sub = parser.add_subparsers(dest="command", required=True)

    for name, func in (
        ("doctor", cmd_doctor),
        ("data", cmd_data),
        ("validate-data", cmd_validate_data),
        ("wait-training", cmd_wait_training),
        ("status", cmd_status),
        ("dashboard", cmd_dashboard),
        ("compare", cmd_compare),
        ("async", cmd_async),
        ("batch", cmd_batch),
        ("load", cmd_load),
        ("scale-demo", cmd_scale_demo),
        ("evidence", cmd_evidence),
        ("verify-clean", cmd_verify_clean),
    ):
        p = sub.add_parser(name)
        p.set_defaults(func=func)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except aws.AwsError as exc:
        log(f"[FAIL] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
