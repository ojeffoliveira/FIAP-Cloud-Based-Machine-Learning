"""Fronteira com a AWS — clientes boto3 compartilhados por todo o trabalho final.

Todo módulo que fala com a AWS passa por aqui: uma consequência é que só este
arquivo sabe o que é boto3, outra é que existe um único lugar para olhar
quando uma chamada precisa de tratamento especial na conta do AWS Academy
(retry, credencial expirada, mensagem em português).

Disciplina de saída, valendo para o trabalho final inteiro: **stdout carrega
resultado** (o dado que alguém vai capturar ou pipar) e **stderr carrega
progresso**. `log()` é o único jeito de escrever progresso — nunca `print()`
direto num módulo que fala com a AWS.
"""

from __future__ import annotations

import functools
import sys
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError, NoCredentialsError, TokenRetrievalError

T = TypeVar("T")


class AwsError(RuntimeError):
    """Falha acionável da AWS: a mensagem diz o que fazer, nunca expõe credencial."""


def log(*args: Any) -> None:
    """Progresso para stderr, com flush imediato. Nunca imprime credencial ou token."""
    print(*args, file=sys.stderr, flush=True)


# O retry padrão do boto3 é tímido para o volume de chamadas curtas que
# `make run` faz em sequência (invoke_endpoint em lote, put_metric_data,
# describe_*). O modo adaptativo absorve throttling sem o aluno ver erro.
_BOTO_CONFIG = BotoConfig(retries={"max_attempts": 8, "mode": "adaptive"})


@functools.lru_cache(maxsize=None)
def client(service: str, region: str) -> Any:
    """Cliente boto3 com região fixa e retry adaptativo. Cacheado por
    (serviço, região): o trabalho final só usa uma região, então isto na
    prática cria um cliente por serviço."""
    return boto3.client(service, region_name=region, config=_BOTO_CONFIG)


# --------------------------------------------------------------------------- #
# Credenciais
# --------------------------------------------------------------------------- #


def check_credentials(region: str) -> dict[str, str]:
    """Chama STS GetCallerIdentity e traduz toda falha de credencial em
    mensagem acionável em português. Ponto de entrada de `make doctor`."""
    try:
        identity = client("sts", region).get_caller_identity()
    except (NoCredentialsError, TokenRetrievalError) as exc:
        raise AwsError(
            "não há credencial da AWS utilizável neste ambiente. Abra o painel do "
            "AWS Academy Learner Lab e cole as credenciais em ~/.aws/credentials."
        ) from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ExpiredToken" or "ExpiredToken" in str(exc):
            raise AwsError(
                "a credencial temporária da AWS expirou. No AWS Academy, abra o "
                "painel do lab e cole as credenciais novamente em "
                "~/.aws/credentials."
            ) from exc
        raise AwsError(f"credencial da AWS rejeitada pelo STS: {exc}") from exc

    return {
        "account": str(identity.get("Account", "")),
        "arn": str(identity.get("Arn", "")),
    }


# --------------------------------------------------------------------------- #
# Espera genérica com timeout e backoff
# --------------------------------------------------------------------------- #


def wait_for(
    poll: Callable[[], T],
    is_done: Callable[[T], bool],
    *,
    timeout_seconds: int,
    description: str,
    initial_delay_seconds: float = 5.0,
    max_delay_seconds: float = 30.0,
) -> T:
    """Repete `poll()` até `is_done(resultado)` ou o timeout vencer.

    Backoff multiplicativo (1.5x, com teto): as primeiras tentativas checam
    rápido, o que importa para operações curtas (alarm, reaction), e as
    tentativas tardias não martelam a API em operações longas (training,
    endpoint InService).
    """
    deadline = time.monotonic() + timeout_seconds
    delay = initial_delay_seconds
    while True:
        result = poll()
        if is_done(result):
            return result
        if time.monotonic() >= deadline:
            raise AwsError(
                f"timeout esperando {description} depois de {timeout_seconds}s."
            )
        log(f"[wait] {description}: ainda não pronto, aguardando {delay:.0f}s")
        time.sleep(delay)
        delay = min(delay * 1.5, max_delay_seconds)


# --------------------------------------------------------------------------- #
# S3
# --------------------------------------------------------------------------- #


def head_object(bucket: str, key: str, region: str) -> dict[str, Any]:
    """Prova que um objeto existe de fato no S3, e não só num plano/expectativa."""
    try:
        return client("s3", region).head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        raise AwsError(f"o objeto s3://{bucket}/{key} não existe ou não está acessível.") from exc


def put_object(
    bucket: str,
    key: str,
    body: bytes,
    region: str,
    *,
    content_type: str | None = None,
) -> None:
    kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key, "Body": body}
    if content_type:
        kwargs["ContentType"] = content_type
    try:
        client("s3", region).put_object(**kwargs)
    except ClientError as exc:
        raise AwsError(f"falha ao gravar s3://{bucket}/{key}: {exc}") from exc


def upload_file(
    bucket: str,
    key: str,
    path: Path,
    region: str,
    *,
    content_type: str | None = None,
) -> None:
    extra_args = {"ContentType": content_type} if content_type else None
    try:
        client("s3", region).upload_file(str(path), bucket, key, ExtraArgs=extra_args)
    except ClientError as exc:
        raise AwsError(f"falha ao subir {path} para s3://{bucket}/{key}: {exc}") from exc


def download_object(bucket: str, key: str, destination: Path, region: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        client("s3", region).download_file(bucket, key, str(destination))
    except ClientError as exc:
        raise AwsError(f"falha ao baixar s3://{bucket}/{key}: {exc}") from exc
    return destination


def list_objects(bucket: str, prefix: str, region: str) -> list[dict[str, Any]]:
    paginator = client("s3", region).get_paginator("list_objects_v2")
    found: list[dict[str, Any]] = []
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            found.extend(page.get("Contents", []))
    except ClientError as exc:
        raise AwsError(f"falha ao listar s3://{bucket}/{prefix}: {exc}") from exc
    return found


# --------------------------------------------------------------------------- #
# SageMaker
# --------------------------------------------------------------------------- #


def describe_training_job(job_name: str, region: str) -> dict[str, Any]:
    try:
        return client("sagemaker", region).describe_training_job(TrainingJobName=job_name)
    except ClientError as exc:
        raise AwsError(f"training job {job_name!r} não encontrado: {exc}") from exc


def describe_endpoint(endpoint_name: str, region: str) -> dict[str, Any]:
    try:
        return client("sagemaker", region).describe_endpoint(EndpointName=endpoint_name)
    except ClientError as exc:
        raise AwsError(f"endpoint {endpoint_name!r} não encontrado: {exc}") from exc


# --------------------------------------------------------------------------- #
# CloudWatch
# --------------------------------------------------------------------------- #


def put_metric_data(
    namespace: str,
    metric_name: str,
    value: float,
    region: str,
    *,
    unit: str = "None",
    dimensions: dict[str, str] | None = None,
) -> None:
    metric_datum: dict[str, Any] = {"MetricName": metric_name, "Value": value, "Unit": unit}
    if dimensions:
        metric_datum["Dimensions"] = [{"Name": k, "Value": v} for k, v in dimensions.items()]
    try:
        client("cloudwatch", region).put_metric_data(Namespace=namespace, MetricData=[metric_datum])
    except ClientError as exc:
        raise AwsError(f"falha ao publicar métrica {metric_name!r} em {namespace!r}: {exc}") from exc
