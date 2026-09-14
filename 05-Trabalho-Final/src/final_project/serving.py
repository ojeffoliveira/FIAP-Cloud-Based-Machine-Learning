"""Adapters de invocacao dos tres candidatos de serving -- Real-Time,
Serverless e Async -- mais o Batch Transform efemero, com uma interface
uniforme para quem consome (candidates.py e workloads.py).

Nenhuma funcao aqui monta o nome de um endpoint por convencao a partir do
prefixo: todo nome vem de `terraform output` (o Terraform ja aplicado no
estagio 2, count = var.enable_serving ? 1 : 0) ou, para o Batch Transform,
do nome do model resolvido pela mesma via. Isso e o que garante que trocar
`student/solution.yaml` e rodar `make run` de novo nunca dispare
`terraform apply`: este modulo so LE o estado ja provisionado, nunca cria
infraestrutura.

Contrato de payload (dado, nao decisao deste modulo): CSV sem cabecalho,
features na ordem exata de `cfg.feature_order`, nunca a coluna de id
(`cfg.id_column`) e nunca o target (`cfg.label_column`) -- o id nao ajuda o
modelo a prever e o target nunca existe no momento da inferencia real.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from . import aws
from .aws import log
from .config import Config


class ServingError(RuntimeError):
    """Erro de contrato do serving: payload malformado, saida da AWS que nao
    bate com o esperado, ou terraform output ausente. Nunca uma falha de
    infraestrutura crua -- essa e `aws.AwsError`."""


# Nome da variavel de ambiente usada quando outro processo (outro agente, ou
# uma outra chamada de `make` deste mesmo aluno) esta com o diretorio do
# Terraform ocupado (lock de state) e ja publicou `terraform output -json`
# num arquivo. Sem essa variavel, o comportamento padrao nunca muda: sempre
# chama `terraform output -json` de verdade. Com ela, os valores ainda vem de
# uma chamada real (de outro processo) -- nunca de uma convencao de nome.
_TF_OUTPUTS_JSON_FILE_ENV = "TF_OUTPUTS_JSON_FILE"


# --------------------------------------------------------------------------- #
# Terraform outputs -- unica porta de entrada para descobrir os endpoints.
# Le `terraform output`, nunca `terraform apply`: e assim que trocar o YAML
# do aluno e rodar `make run` de novo nao reprovisiona nada.
# --------------------------------------------------------------------------- #


def terraform_outputs(cfg: Config) -> dict[str, Any]:
    override_path = os.environ.get(_TF_OUTPUTS_JSON_FILE_ENV)
    if override_path:
        try:
            raw = json.loads(Path(override_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ServingError(f"{_TF_OUTPUTS_JSON_FILE_ENV}={override_path!r} nao e um JSON valido: {exc}") from exc
        return {key: value.get("value") for key, value in raw.items()}

    try:
        completed = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=str(cfg.terraform_dir),
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise ServingError("binario do terraform nao encontrado no PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise ServingError(f"terraform output falhou em {cfg.terraform_dir}: {exc.stderr.strip()}") from exc
    raw = json.loads(completed.stdout or "{}")
    return {key: value.get("value") for key, value in raw.items()}


def require_output(outputs: dict[str, Any], key: str) -> str:
    value = outputs.get(key)
    if not value:
        raise ServingError(
            f"o terraform output {key!r} esta vazio. Rode o estagio 2 do apply "
            "(enable_serving=true, com o model_artifact_s3_uri resolvido) antes "
            "de comparar ou executar candidatos."
        )
    return str(value)


# --------------------------------------------------------------------------- #
# Payload: CSV -> linhas na ordem de cfg.feature_order, sem id, sem target.
# --------------------------------------------------------------------------- #


def build_payload_rows(csv_path: Path, cfg: Config) -> tuple[list[str], list[str]]:
    """Le um CSV com cabecalho (id + features, sem rotulo -- contrato de
    `data_contract.py`/`dataset.py`) e devolve `(ids, linhas_de_payload)`.

    As linhas de payload nunca contem `cfg.id_column` nem `cfg.label_column`
    -- so as features, na ordem exata de `cfg.feature_order`, procuradas pelo
    nome da coluna (nao pela posicao no arquivo), para o payload nunca
    depender de o CSV de origem ja estar na ordem certa.
    """
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ServingError(f"{csv_path} esta vazio (sem cabecalho).") from exc

        col_index = {name: idx for idx, name in enumerate(header)}
        missing = [name for name in cfg.feature_order if name not in col_index]
        if missing:
            raise ServingError(f"{csv_path}: faltam colunas de feature no cabecalho: {missing}.")
        id_idx = col_index.get(cfg.id_column)

        ids: list[str] = []
        rows: list[str] = []
        for raw in reader:
            if not raw:
                continue
            if id_idx is not None:
                ids.append(raw[id_idx])
            rows.append(",".join(raw[col_index[name]] for name in cfg.feature_order))

    return ids, rows


def _parse_probabilities(payload: str, expected: int | None = None) -> list[float]:
    """Interpreta a resposta CSV (uma probabilidade por linha/token) do
    XGBoost nativo do SageMaker, e recusa qualquer valor fora de [0,1] --
    contrato de dados, nao decisao de negocio."""
    tokens: list[str] = []
    for line in payload.replace("\r", "").split("\n"):
        tokens.extend(token for token in line.split(",") if token.strip())
    values: list[float] = []
    for token in tokens:
        value = float(token)
        if not (0.0 <= value <= 1.0):
            raise ServingError(f"probabilidade fora de [0,1] na resposta do endpoint: {value}")
        values.append(value)
    if expected is not None and len(values) != expected:
        raise ServingError(f"o endpoint devolveu {len(values)} probabilidades para {expected} linhas enviadas.")
    return values


def _split_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ServingError(f"nao e uma URI do S3: {uri!r}")
    bucket, _, key = uri[len("s3://") :].partition("/")
    if not bucket or not key:
        raise ServingError(f"URI do S3 sem bucket ou sem key: {uri!r}")
    return bucket, key


def _object_exists(bucket: str, key: str, region: str) -> dict[str, Any] | None:
    try:
        return aws.head_object(bucket, key, region)
    except aws.AwsError:
        return None


# --------------------------------------------------------------------------- #
# Latencia -- compartilhado por candidates.py (compare) e workloads.py
# (atendimento).
# --------------------------------------------------------------------------- #


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def latency_stats(samples_ms: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(percentile(samples_ms, 50), 3),
        "p95_ms": round(percentile(samples_ms, 95), 3),
    }


# --------------------------------------------------------------------------- #
# Real-Time / Serverless -- mesma chamada sincrona de InvokeEndpoint; o que
# muda entre os dois candidatos e a infraestrutura por tras do nome do
# endpoint, nunca o formato da chamada. Duas funcoes publicas distintas
# (nao uma so parametrizada) porque cada pattern e um adapter proprio na
# interface que candidates.py/workloads.py chamam.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InvokeResult:
    probabilities: list[float]
    elapsed_ms: float


def _invoke_sync(endpoint_name: str, body: str, region: str) -> InvokeResult:
    runtime = aws.client("sagemaker-runtime", region)
    started = time.monotonic()
    try:
        response = runtime.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="text/csv",
            Accept="text/csv",
            Body=body.encode("utf-8"),
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        raise aws.AwsError(f"invoke_endpoint falhou em {endpoint_name!r} ({code}): {exc}") from exc
    elapsed_ms = (time.monotonic() - started) * 1000.0
    payload = response["Body"].read().decode("utf-8").strip()
    expected = len(body.strip().split("\n")) if body.strip() else 0
    probabilities = _parse_probabilities(payload, expected=expected)
    return InvokeResult(probabilities=probabilities, elapsed_ms=elapsed_ms)


def invoke_realtime(endpoint_name: str, body: str, region: str) -> InvokeResult:
    """Candidato Real-Time: instancia persistente, InvokeEndpoint sincrono."""
    return _invoke_sync(endpoint_name, body, region)


def invoke_serverless(endpoint_name: str, body: str, region: str) -> InvokeResult:
    """Candidato Serverless: mesma chamada sincrona, capacidade gerenciada
    pela AWS -- a diferenca aparece na latencia da primeira chamada apos
    ociosidade (cold start), nao no formato da requisicao."""
    return _invoke_sync(endpoint_name, body, region)


# --------------------------------------------------------------------------- #
# Async -- submissao e coleta desacopladas pelo S3, sem SNS (o ACADEMY.md
# confirma que nenhum lab anterior usa notification_config aqui): o consumidor
# faz polling direto do objeto de saida.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AsyncSubmission:
    inference_id: str
    input_uri: str
    output_uri: str


@dataclass(frozen=True)
class AsyncResult:
    probabilities: list[float]
    duration_seconds: float


def submit_async(endpoint_name: str, bucket: str, input_prefix: str, rows: list[str], region: str) -> AsyncSubmission:
    """Sobe o payload para um caminho novo no S3 (carimbado com o timestamp,
    para nunca colidir com uma execucao anterior) e chama InvokeEndpointAsync
    -- a chamada retorna na hora, com o InferenceId e o local onde o
    resultado vai aparecer; a inferencia ainda nao rodou nesse ponto."""
    body = "\n".join(rows) + "\n" if rows else ""
    key = f"{input_prefix.rstrip('/')}/{int(time.time())}.csv"
    aws.put_object(bucket, key, body.encode("utf-8"), region, content_type="text/csv")
    input_uri = f"s3://{bucket}/{key}"

    runtime = aws.client("sagemaker-runtime", region)
    try:
        response = runtime.invoke_endpoint_async(
            EndpointName=endpoint_name,
            InputLocation=input_uri,
            ContentType="text/csv",
            Accept="text/csv",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        raise aws.AwsError(f"invoke_endpoint_async falhou em {endpoint_name!r} ({code}): {exc}") from exc

    return AsyncSubmission(
        inference_id=response["InferenceId"],
        input_uri=input_uri,
        output_uri=response["OutputLocation"],
    )


def collect_async(
    submission: AsyncSubmission,
    region: str,
    *,
    poll_seconds: float = 10.0,
    timeout_seconds: int = 600,
) -> AsyncResult:
    """Poll direto do objeto de saida no S3 -- nunca SNS. `aws.wait_for` faz o
    backoff; assim que o objeto existe, baixa e conta as probabilidades."""
    bucket, key = _split_s3_uri(submission.output_uri)
    started = time.monotonic()

    aws.wait_for(
        lambda: _object_exists(bucket, key, region),
        lambda head: head is not None,
        timeout_seconds=timeout_seconds,
        description=f"saida assincrona em s3://{bucket}/{key}",
        initial_delay_seconds=poll_seconds,
        max_delay_seconds=max(poll_seconds, 30.0),
    )
    duration_seconds = time.monotonic() - started

    body = aws.client("s3", region).get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    probabilities = _parse_probabilities(body)
    return AsyncResult(probabilities=probabilities, duration_seconds=duration_seconds)


# --------------------------------------------------------------------------- #
# Batch Transform -- job efemero criado por boto3, nunca por Terraform (o
# ACADEMY.md confirma: nao existe aws_sagemaker_transform_job no provider).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BatchResult:
    job_name: str
    status: str
    output_uri: str
    probabilities: list[float]
    duration_seconds_reported_by_api: float | None


def run_batch_transform(
    model_name: str,
    bucket: str,
    input_prefix: str,
    output_prefix: str,
    rows: list[str],
    region: str,
    *,
    instance_type: str,
    max_concurrent_transforms: int = 1,
    max_payload_in_mb: int = 1,
    batch_strategy: str = "MultiRecord",
    timeout_seconds: int = 900,
) -> BatchResult:
    """Sobe o payload, cria o TransformJob, espera Completed e LE o prefixo de
    saida no S3 em vez de assumir o nome do arquivo -- o SageMaker decide
    esse nome, nunca uma convencao deste modulo.

    `max_concurrent_transforms * max_payload_in_mb <= 100` e exigencia da API
    (mesmo limite documentado no lab 03 / ACADEMY.md item 6).
    """
    timestamp = int(time.time())
    body = "\n".join(rows) + "\n" if rows else ""
    input_key = f"{input_prefix.rstrip('/')}/{timestamp}.csv"
    aws.put_object(bucket, input_key, body.encode("utf-8"), region, content_type="text/csv")
    input_uri = f"s3://{bucket}/{input_key}"

    output_prefix_run = f"{output_prefix.rstrip('/')}/{timestamp}/"
    output_uri = f"s3://{bucket}/{output_prefix_run}"
    job_name = f"final-batch-{timestamp}"

    sagemaker = aws.client("sagemaker", region)
    log(f"[batch] criando o transform job {job_name}")
    try:
        sagemaker.create_transform_job(
            TransformJobName=job_name,
            ModelName=model_name,
            MaxConcurrentTransforms=max_concurrent_transforms,
            MaxPayloadInMB=max_payload_in_mb,
            BatchStrategy=batch_strategy,
            TransformInput={
                "DataSource": {"S3DataSource": {"S3DataType": "S3Prefix", "S3Uri": input_uri}},
                "ContentType": "text/csv",
                "SplitType": "Line",
            },
            TransformOutput={"S3OutputPath": output_uri, "Accept": "text/csv", "AssembleWith": "Line"},
            TransformResources={"InstanceType": instance_type, "InstanceCount": 1},
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        raise aws.AwsError(f"create_transform_job falhou para {job_name!r} ({code}): {exc}") from exc

    def _poll() -> dict[str, Any]:
        return sagemaker.describe_transform_job(TransformJobName=job_name)

    description = aws.wait_for(
        _poll,
        lambda d: d["TransformJobStatus"] in {"Completed", "Failed", "Stopped"},
        timeout_seconds=timeout_seconds,
        description=f"transform job {job_name}",
        initial_delay_seconds=15.0,
        max_delay_seconds=30.0,
    )
    status = description["TransformJobStatus"]
    if status != "Completed":
        raise aws.AwsError(f"o transform job {job_name} terminou como {status}, nao Completed.")

    output_objects = aws.list_objects(bucket, output_prefix_run, region)
    if len(output_objects) != 1:
        keys = [obj.get("Key") for obj in output_objects]
        raise ServingError(
            f"esperava exatamente 1 objeto de saida em s3://{bucket}/{output_prefix_run}, encontrei {keys}."
        )
    output_key = output_objects[0]["Key"]
    body_text = aws.client("s3", region).get_object(Bucket=bucket, Key=output_key)["Body"].read().decode("utf-8")
    probabilities = _parse_probabilities(body_text)

    duration_seconds_reported_by_api = None
    start_time = description.get("TransformStartTime")
    end_time = description.get("TransformEndTime")
    if start_time and end_time:
        duration_seconds_reported_by_api = (end_time - start_time).total_seconds()

    return BatchResult(
        job_name=job_name,
        status=status,
        output_uri=f"s3://{bucket}/{output_key}",
        probabilities=probabilities,
        duration_seconds_reported_by_api=duration_seconds_reported_by_api,
    )


# --------------------------------------------------------------------------- #
# Status dos tres candidatos, pela API -- ponto de entrada de `make status`
# e da evidencia `artifacts/evidence/serving.json` (escrita por workloads.py).
# --------------------------------------------------------------------------- #


def collect_endpoint_status(cfg: Config) -> dict[str, Any]:
    outputs = terraform_outputs(cfg)
    status: dict[str, Any] = {}
    for pattern, output_key in (
        ("realtime", "realtime_endpoint_name"),
        ("serverless", "serverless_endpoint_name"),
        ("async", "async_endpoint_name"),
    ):
        name = outputs.get(output_key)
        if not name:
            status[pattern] = {"exists": False}
            continue
        description = aws.describe_endpoint(name, cfg.region)
        variants = description.get("ProductionVariants", [])
        status[pattern] = {
            "exists": True,
            "name": name,
            "status": description["EndpointStatus"],
            "current_instance_count": variants[0].get("CurrentInstanceCount") if variants else None,
        }
    return status


def cmd_status(cfg: Config, args: Any) -> int:
    status = collect_endpoint_status(cfg)
    for pattern, entry in status.items():
        if entry.get("exists"):
            log(f"[status] {pattern:<10} {entry['name']}: {entry['status']}")
        else:
            log(f"[status] {pattern:<10} ainda nao provisionado (enable_serving=false ou apply pendente)")
    print(json.dumps(status, indent=2, ensure_ascii=False, sort_keys=True))
    failed = any(entry.get("status") == "Failed" for entry in status.values())
    return 1 if failed else 0
