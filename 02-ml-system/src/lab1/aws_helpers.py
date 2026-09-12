"""Cola fina de Boto3 e Terraform compartilhada pelos scripts de controle.

Duas regras moldam este módulo:

1. Nada aqui imprime ou devolve credencial. A identidade é reportada só como ID
   da conta / ARN, que é o que a evidência precisa e o que um print pode mostrar
   sem risco.
2. A região é afirmada, não assumida. O lab do Academy só permite `us-east-1`, e
   uma região errada em silêncio produz erros confusos de "recurso não
   encontrado" muito mais tarde, então toda sessão é validada na construção.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Iterable

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, NoCredentialsError, TokenRetrievalError

from lab1.config import TERRAFORM_DIR, log

# Retry importa aqui: contas do Academy são compartilhadas e throttling é comum.
BOTO_CONFIG = Config(retries={"max_attempts": 10, "mode": "adaptive"})

TERMINAL_TRAINING_STATUSES = {"Completed", "Failed", "Stopped"}
TERMINAL_ENDPOINT_STATUSES = {"InService", "Failed", "OutOfService"}


class AwsError(RuntimeError):
    """Falha acionável ao falar com a AWS - a mensagem é para o aluno ler."""


def make_session(region: str, profile: str | None = None) -> boto3.session.Session:
    session = boto3.session.Session(profile_name=profile) if profile else boto3.session.Session()
    resolved = session.region_name
    if resolved and resolved != region:
        raise AwsError(
            f"a região da sessão é {resolved!r}, mas este lab exige {region!r}. "
            f"Exporte AWS_DEFAULT_REGION={region} ou corrija o profile."
        )
    if not resolved:
        # Profile sem região é comum no Academy; fixar explicitamente em vez de
        # deixar cada client adivinhar.
        session = boto3.session.Session(profile_name=profile, region_name=region)
    return session


def client(session: boto3.session.Session, service: str) -> Any:
    return session.client(service, config=BOTO_CONFIG)


def whoami(session: boto3.session.Session) -> dict[str, str]:
    """Identidade de quem chama, sem segredos. Falha com mensagem legível quando expira."""
    try:
        identity = client(session, "sts").get_caller_identity()
    except (NoCredentialsError, TokenRetrievalError) as exc:
        raise AwsError(
            "não há credencial da AWS utilizável. No AWS Academy, reabra o lab e copie "
            "as credenciais novas para o seu ambiente ou profile."
        ) from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"ExpiredToken", "InvalidClientTokenId", "RequestExpired"}:
            raise AwsError(
                f"credenciais da AWS recusadas ({code}). O token de sessão do Academy "
                "expira; inicie o lab de novo e atualize as credenciais."
            ) from exc
        raise
    return {
        "account_id": identity["Account"],
        "arn": identity["Arn"],
        "user_id_prefix": identity["UserId"].split(":")[0],
    }


def resolve_lab_role(session: boto3.session.Session, role_name: str) -> str:
    """Devolve o ARN da LabRole. O Academy proíbe criar role, então ela precisa existir."""
    try:
        return client(session, "iam").get_role(RoleName=role_name)["Role"]["Arn"]
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "NoSuchEntity":
            raise AwsError(
                f"role {role_name!r} não encontrada. Este lab depende da role já provisionada "
                "pelo Academy e não pode criar role de IAM por conta própria."
            ) from exc
        if code == "AccessDenied":
            # Algumas policies do Academy negam iam:GetRole e ainda assim permitem PassRole.
            identity = whoami(session)
            arn = f"arn:aws:iam::{identity['account_id']}:role/{role_name}"
            log(f"[aviso] iam:GetRole negado; assumindo que {arn} existe (padrão do Academy)")
            return arn
        raise


# --------------------------------------------------------------------------- #
# Terraform outputs
# --------------------------------------------------------------------------- #


def terraform_outputs(directory: str | None = None) -> dict[str, Any]:
    """Lê `terraform output -json` e achata para valores simples."""
    cwd = directory or str(TERRAFORM_DIR)
    try:
        completed = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise AwsError("binário do terraform não encontrado no PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise AwsError(f"terraform output falhou em {cwd}: {exc.stderr.strip()}") from exc
    raw = json.loads(completed.stdout or "{}")
    return {key: value.get("value") for key, value in raw.items()}


def require_output(outputs: dict[str, Any], key: str) -> Any:
    if key not in outputs or outputs[key] in (None, ""):
        raise AwsError(
            f"o terraform output {key!r} não está disponível. Rode `make apply` primeiro "
            "(o estágio de serving só existe depois do deploy)."
        )
    return outputs[key]


# --------------------------------------------------------------------------- #
# SageMaker
# --------------------------------------------------------------------------- #


def describe_training_job(session: boto3.session.Session, job_name: str) -> dict[str, Any]:
    return client(session, "sagemaker").describe_training_job(TrainingJobName=job_name)


def wait_training_job(
    session: boto3.session.Session,
    job_name: str,
    poll_seconds: int = 20,
    timeout_seconds: int = 3600,
) -> dict[str, Any]:
    """Consulta até o job chegar a um estado terminal, narrando o progresso em stderr."""
    sagemaker = client(session, "sagemaker")
    deadline = time.monotonic() + timeout_seconds
    last_secondary = ""
    while True:
        description = sagemaker.describe_training_job(TrainingJobName=job_name)
        status = description["TrainingJobStatus"]
        secondary = description.get("SecondaryStatus", "")
        if secondary != last_secondary:
            log(f"[training] {job_name}: {status} / {secondary}")
            last_secondary = secondary
        if status in TERMINAL_TRAINING_STATUSES:
            return description
        if time.monotonic() > deadline:
            raise AwsError(
                f"o training job {job_name} continua {status}/{secondary} depois de "
                f"{timeout_seconds}s; confira o console do SageMaker ou os logs do CloudWatch"
            )
        time.sleep(poll_seconds)


def describe_endpoint(session: boto3.session.Session, endpoint_name: str) -> dict[str, Any]:
    return client(session, "sagemaker").describe_endpoint(EndpointName=endpoint_name)


def object_exists(session: boto3.session.Session, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        head = client(session, "s3").head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    return {"content_length": int(head["ContentLength"]), "etag": head["ETag"].strip('"')}


def split_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise AwsError(f"não é uma URI do S3: {uri!r}")
    bucket, _, key = uri[len("s3://") :].partition("/")
    if not bucket or not key:
        raise AwsError(f"URI do S3 sem bucket ou sem key: {uri!r}")
    return bucket, key


def invoke_endpoint_csv(
    session: boto3.session.Session, endpoint_name: str, body: str
) -> list[float]:
    """Envia CSV sem cabeçalho e lê uma probabilidade por linha de entrada."""
    runtime = client(session, "sagemaker-runtime")
    try:
        response = runtime.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="text/csv",
            Accept="text/csv",
            Body=body.encode("utf-8"),
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        raise AwsError(
            f"invoke_endpoint falhou em {endpoint_name!r} ({code}): "
            f"{exc.response.get('Error', {}).get('Message', '')}"
        ) from exc
    payload = response["Body"].read().decode("utf-8").strip()
    return parse_csv_probabilities(payload, expected=len(body.split("\n")))


def parse_csv_probabilities(payload: str, expected: int | None = None) -> list[float]:
    """O XGBoost nativo responde com probabilidades separadas por newline ou vírgula."""
    tokens: list[str] = []
    for line in payload.replace("\r", "").split("\n"):
        tokens.extend(token for token in line.split(",") if token.strip())
    values: list[float] = []
    for token in tokens:
        value = float(token)
        if not (0.0 <= value <= 1.0):
            raise AwsError(f"probabilidade fora de [0,1]: {value}")
        values.append(value)
    if expected is not None and len(values) != expected:
        raise AwsError(f"o endpoint devolveu {len(values)} probabilidades para {expected} linhas")
    return values


def batched(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def json_safe(value: Any) -> Any:
    """Torna a resposta do Boto3 serializável em JSON (os timestamps são datetime)."""
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
