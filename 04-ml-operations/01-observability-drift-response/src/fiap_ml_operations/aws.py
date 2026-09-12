"""Fronteira com a AWS — clientes boto3, outputs do Terraform e invocação do endpoint.

Todo acesso à AWS do lab passa por aqui. Duas consequências úteis: o resto do
código não sabe o que é boto3, e existe um único lugar para olhar quando uma
chamada precisa de tratamento especial na conta do Academy.

Disciplina de saída, valendo para o lab inteiro: **stdout carrega resultado**
(o JSON que alguém vai capturar ou pipar) e **stderr carrega progresso**. Assim
`make status > arquivo.json` produz um arquivo válido, sem narração no meio.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import boto3
import numpy as np
from botocore.config import Config
from botocore.exceptions import ClientError, NoCredentialsError, TokenRetrievalError

from . import config


def log(*args: Any) -> None:
    """Progresso para stderr. Nunca imprime credencial nem token."""
    print(*args, file=sys.stderr, flush=True)


def emit(payload: Any) -> None:
    """Resultado para stdout, sempre como JSON."""
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


class LabError(RuntimeError):
    """Falha acionável do laboratório: a mensagem diz ao aluno o que fazer."""


# O retry padrão do boto3 é tímido para o volume de chamadas curtas que o lab faz
# em sequência. O modo adaptativo respeita throttling sem o aluno ver erro.
_BOTO_CONFIG = Config(retries={"max_attempts": 8, "mode": "adaptive"})


@functools.lru_cache(maxsize=None)
def client(service: str) -> Any:
    region = config.load_config()["region"]
    return boto3.client(service, region_name=region, config=_BOTO_CONFIG)


# --------------------------------------------------------------------------- #
# Outputs do Terraform
# --------------------------------------------------------------------------- #


def _terraform_binary() -> str:
    """Respeita o TF exportado pelo Makefile; cai no PATH quando ausente."""
    return os.environ.get("TF") or "terraform"


@functools.lru_cache(maxsize=1)
def terraform_outputs() -> dict[str, Any]:
    """Lê os outputs do state. É a fonte de verdade dos nomes de recurso.

    Nada de reconstruir nome de recurso no Python: o sufixo do ciclo de vida mora
    no state do Terraform, e recalculá-lo aqui seria inventar uma segunda verdade
    que sai de sincronia no primeiro `destroy`.
    """
    result = subprocess.run(  # noqa: S603 - binário e argumentos são fixos
        [_terraform_binary(), f"-chdir={config.TERRAFORM_DIR}", "output", "-json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise LabError(
            "não foi possível ler os outputs do Terraform. Rode `make apply` antes "
            f"deste comando.\nsaída do terraform: {result.stderr.strip()}"
        )
    raw = json.loads(result.stdout or "{}")
    return {key: value.get("value") for key, value in raw.items()}


def output(name: str) -> Any:
    """Um output específico, com erro acionável quando o estágio errado está aplicado."""
    outputs = terraform_outputs()
    if name not in outputs:
        raise LabError(f"output {name!r} não existe no state. Rode `make apply`.")
    value = outputs[name]
    if value in ("", None):
        raise LabError(
            f"output {name!r} está vazio: o estágio de serving ainda não foi aplicado. "
            "Rode `make apply`."
        )
    return value


# --------------------------------------------------------------------------- #
# Identidade e pré-condições
# --------------------------------------------------------------------------- #


def caller_identity() -> dict[str, str]:
    # NoCredentialsError e TokenRetrievalError não derivam de ClientError: sem estes dois
    # primeiro, o aluno sem credencial colada vê traceback do botocore no lugar do [FAIL].
    try:
        return client("sts").get_caller_identity()
    except (NoCredentialsError, TokenRetrievalError) as exc:
        raise LabError(
            "não há credencial da AWS utilizável neste ambiente. Abra o painel do lab no "
            "AWS Academy e cole as credenciais em ~/.aws/credentials."
        ) from exc
    except ClientError as exc:
        raise LabError(
            "credencial da AWS inválida ou expirada. No AWS Academy, abra o painel do "
            "lab e copie as credenciais novamente para ~/.aws/credentials."
        ) from exc


def role_arn(role_name: str) -> str:
    try:
        return client("iam").get_role(RoleName=role_name)["Role"]["Arn"]
    except (NoCredentialsError, TokenRetrievalError) as exc:
        raise LabError(
            "não há credencial da AWS utilizável neste ambiente. Abra o painel do lab no "
            "AWS Academy e cole as credenciais em ~/.aws/credentials."
        ) from exc
    except ClientError as exc:
        raise LabError(
            f"não foi possível ler a role {role_name!r}. Ela é criada pelo próprio "
            "Academy; se não existe, o lab foi aberto em uma conta diferente."
        ) from exc


# --------------------------------------------------------------------------- #
# Training job
# --------------------------------------------------------------------------- #

_TERMINAL_TRAINING_STATES = {"Completed", "Failed", "Stopped"}


def wait_training_job(job_name: str, poll_seconds: int = 15) -> dict[str, Any]:
    """Espera o training job terminar e devolve o DescribeTrainingJob final.

    Existe porque o recurso do Terraform retorna assim que o job entra em
    InProgress: um `apply` verde não é um modelo treinado. Este é o portão entre os
    dois estágios.
    """
    sagemaker = client("sagemaker")
    while True:
        described = sagemaker.describe_training_job(TrainingJobName=job_name)
        status = described["TrainingJobStatus"]
        if status in _TERMINAL_TRAINING_STATES:
            break
        secondary = described.get("SecondaryStatus", "")
        log(f"[wait] training job {status}/{secondary}; aguardando {poll_seconds}s")
        time.sleep(poll_seconds)

    if status != "Completed":
        motivo = described.get("FailureReason", "sem motivo informado pela API")
        raise LabError(f"training job terminou como {status}: {motivo}")

    return described


def head_object(uri: str) -> dict[str, Any]:
    """Prova que um objeto do S3 existe de fato, e não só no plano de alguém."""
    if not uri.startswith("s3://"):
        raise LabError(f"URI do S3 inválida: {uri!r}")
    bucket, _, key = uri[len("s3://") :].partition("/")
    try:
        return client("s3").head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        raise LabError(f"o objeto {uri} não existe ou não está acessível.") from exc


def write_handoff(artifact_uri: str) -> Path:
    """Escreve o arquivo de passagem de bastão que o estágio 2 do apply consome.

    O arquivo termina em `.auto.tfvars.json`, então o Terraform o carrega sozinho no
    apply seguinte — o Makefile não precisa passar `-var`.

    Ele carrega DUAS chaves, e as duas importam. A URI é o que o Model consome; o
    `deploy_serving` é o portão. Escrever os dois no mesmo arquivo, aqui, cria o
    invariante que o lab depende: o estágio de serving só pode ser ligado por quem
    acabou de provar que o artefato existe. Se este arquivo trouxesse apenas a URI, o
    `deploy_serving` ficaria no default `false` e o estágio 2 aplicaria em silêncio
    sem criar endpoint, dashboard, alarme nem Lambda — um apply verde que não
    entregou nada.
    """
    path = config.TERRAFORM_DIR / "artifact.auto.tfvars.json"
    path.write_text(
        json.dumps(
            {"deploy_serving": True, "model_artifact_uri": artifact_uri}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# Endpoint em tempo real
# --------------------------------------------------------------------------- #


def endpoint_status(endpoint_name: str) -> str:
    try:
        return client("sagemaker").describe_endpoint(EndpointName=endpoint_name)[
            "EndpointStatus"
        ]
    except ClientError as exc:
        raise LabError(f"endpoint {endpoint_name!r} não encontrado.") from exc


def require_endpoint_in_service(endpoint_name: str) -> None:
    status = endpoint_status(endpoint_name)
    if status != "InService":
        raise LabError(
            f"o endpoint {endpoint_name} está {status}, não InService. "
            "Rode `make status` e espere o provisionamento terminar."
        )


def invoke_endpoint_csv(endpoint_name: str, rows: list[list[float]]) -> np.ndarray:
    """Invoca o endpoint em lotes e devolve as probabilidades, na ordem de entrada.

    O XGBoost nativo aceita `text/csv` com várias linhas por chamada e responde uma
    probabilidade por linha, separadas por vírgula ou quebra de linha. Enviar em
    lotes em vez de linha a linha é o que mantém o gráfico de Invocations legível e
    o lab rápido — mas a ORDEM da resposta é a ordem do envio, e é por isso que os
    lotes são processados em sequência e concatenados.
    """
    runtime = client("sagemaker-runtime")
    batch_rows = int(config.load_config()["inference"]["batch_rows"])
    scores: list[float] = []

    for start in range(0, len(rows), batch_rows):
        chunk = rows[start : start + batch_rows]
        payload = "\n".join(",".join(f"{value:g}" for value in row) for row in chunk)
        response = runtime.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="text/csv",
            Accept="text/csv",
            Body=payload.encode("utf-8"),
        )
        body = response["Body"].read().decode("utf-8").strip()
        parsed = [float(token) for token in body.replace("\n", ",").split(",") if token]
        if len(parsed) != len(chunk):
            raise LabError(
                f"o endpoint devolveu {len(parsed)} scores para {len(chunk)} linhas "
                "enviadas: o payload e a resposta saíram de sincronia."
            )
        scores.extend(parsed)

    return np.array(scores, dtype=float)


# --------------------------------------------------------------------------- #
# S3
# --------------------------------------------------------------------------- #


def list_objects(bucket: str, prefix: str) -> list[dict[str, Any]]:
    paginator = client("s3").get_paginator("list_objects_v2")
    found: list[dict[str, Any]] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        found.extend(page.get("Contents", []))
    return found


def get_json_object(bucket: str, key: str) -> dict[str, Any]:
    body = client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body.decode("utf-8"))


def bucket_exists(bucket: str) -> bool:
    try:
        client("s3").head_bucket(Bucket=bucket)
        return True
    except ClientError:
        return False
