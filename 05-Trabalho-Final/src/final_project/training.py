"""Treino em dois estágios e handoff do artefato — o modelo XGBoost do
Trabalho Final, criado por boto3 (nunca por recurso Terraform, ver
`terraform/training.tf`).

Três pontos de entrada, chamados pelo dispatcher do CLI (`scripts/final.py`,
A3), cada um cobrindo uma fase:

* `cmd_train`      — sobe train/validation para o S3 e cria o Training Job.
* `cmd_training_status` — `DescribeTrainingJob` avulso, para acompanhar sem
  bloquear.
* `cmd_artifact`   — o handoff: espera o estado terminal, lê a URI do
  artefato só da API, confirma com `HeadObject` e escreve o
  `.generated/artifact.auto.tfvars.json` que o estágio 2 do Terraform
  consome.

Regra dura do handoff: a URI do artefato nunca é montada por convenção de
caminho (`s3://bucket/prefixo/job/output/model.tar.gz`). Ela só existe depois
de `ModelArtifacts.S3ModelArtifacts` vir do `DescribeTrainingJob` e o
`HeadObject` provar que o objeto está lá — se a API não confirmar, este
módulo falha alto em vez de seguir para o serving.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from . import aws
from .config import Config

# --------------------------------------------------------------------------- #
# Saída: stdout carrega resultado, stderr carrega progresso (regra do
# trabalho final inteiro).
# --------------------------------------------------------------------------- #


def log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


def emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


# --------------------------------------------------------------------------- #
# Hiperparâmetros, imagem e capacidade — espelham exatamente
# `terraform/variables.tf` (hyperparameters/training_image/instance_type/
# volume_size_in_gb/max_runtime_in_seconds). Duplicados de propósito, não
# lidos de lá: o job nasce por boto3 e o Terraform desse arquivo não cria
# nada que possa carregar esses valores para o lado Python (mesma lógica que
# `psi_threshold` já usa entre `config/scenario.yaml` e `variables.tf` — dois
# defaults que precisam ficar em sincronia manual). Mudar um lado sem o outro
# quebra a linhagem churn-v1 que os labs anteriores já validaram.
TRAINING_IMAGE = "683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-xgboost:1.7-1"
INSTANCE_TYPE = "ml.m5.large"
VOLUME_SIZE_IN_GB = 30
MAX_RUNTIME_IN_SECONDS = 900
HYPERPARAMETERS: dict[str, str] = {
    "objective": "binary:logistic",
    "eval_metric": "auc",
    "num_round": "50",
    "max_depth": "4",
    "eta": "0.10",
    "subsample": "0.90",
    "colsample_bytree": "0.90",
    "verbosity": "1",
}

# Mesmo default de `var.execution_role_name` — o trabalho final nunca cria
# role de IAM, só lê a que o Academy já provisionou.
LAB_ROLE_NAME = "LabRole"

# Mesmas chaves de `local.s3_prefixes` (terraform/locals.tf, A0). Duplicadas
# aqui porque não há canal de Terraform para Python neste ponto — o job é
# boto3, não um resource que exportaria o prefixo resolvido.
DATA_PREFIX = "data"
ARTIFACTS_PREFIX = "artifacts"

TRAIN_FILENAME = "train.csv"
VALIDATION_FILENAME = "validation.csv"

TRAINING_TERMINAL_STATES = ("Completed", "Failed", "Stopped")

# Timeout de espera do estado terminal: a folga sobre `MAX_RUNTIME_IN_SECONDS`
# cobre o tempo de provisionamento da instância antes do treino em si começar.
DEFAULT_WAIT_TIMEOUT_SECONDS = MAX_RUNTIME_IN_SECONDS + 300


def _evidence_path(cfg: Config) -> Path:
    return cfg.evidence_dir / "training.json"


def _artifact_evidence_path(cfg: Config) -> Path:
    return cfg.evidence_dir / "artifact.json"


def _handoff_path(cfg: Config) -> Path:
    return cfg.generated_dir / "artifact.auto.tfvars.json"


def _json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bucket_name(cfg: Config, account_id: str) -> str:
    # Mesma fórmula de `local.bucket_name` em terraform/locals.tf: prefixo +
    # id da conta, para o nome ficar globalmente único sem precisar de
    # random_id extra do lado Python.
    return f"{cfg.prefix}-{account_id}"


def _job_name(cfg: Config) -> str:
    # SageMaker recusa reutilizar nome de training job já existente na conta
    # (mesmo problema que motivou `random_id.lifecycle` em locals.tf) — o
    # timestamp garante um nome novo a cada `train`, sem precisar de state.
    return f"{cfg.prefix}-train-{time.strftime('%Y%m%d%H%M%S')}"


def _split_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise aws.AwsError(f"URI de artefato inesperada (não começa com s3://): {uri}")
    bucket, _, key = uri[len("s3://") :].partition("/")
    if not bucket or not key:
        raise aws.AwsError(f"URI de artefato malformada, sem bucket ou key: {uri}")
    return bucket, key


def _load_last_job_name(cfg: Config) -> str | None:
    path = _evidence_path(cfg)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    job_name = data.get("training_job_name")
    return str(job_name) if job_name else None


def _resolve_job_name(cfg: Config, args: Any) -> str:
    job_name = getattr(args, "job_name", None)
    if job_name:
        return str(job_name)
    job_name = _load_last_job_name(cfg)
    if not job_name:
        raise aws.AwsError(
            "nenhum training job conhecido. Informe --job-name ou rode "
            "`final.py train` primeiro (ele grava o nome em "
            f"{_evidence_path(cfg)})."
        )
    return job_name


def _role_arn(cfg: Config) -> str:
    try:
        role = aws.client("iam", cfg.region).get_role(RoleName=LAB_ROLE_NAME)
    except ClientError as exc:
        raise aws.AwsError(
            f"não foi possível ler a role {LAB_ROLE_NAME!r} do Academy: {exc}. "
            "Confira se a credencial ativa é a do Learner Lab (LabRole precisa existir)."
        ) from exc
    return str(role["Role"]["Arn"])


def _upload_channel(cfg: Config, bucket: str, filename: str) -> str:
    local_path = cfg.data_dir / filename
    if not local_path.exists():
        raise aws.AwsError(
            f"dado de treino ausente: {local_path}. Rode `final.py data` antes de `train`."
        )
    key = f"{DATA_PREFIX}/{filename}"
    log(f"[train] subindo {local_path.name} para s3://{bucket}/{key}")
    aws.upload_file(bucket, key, local_path, cfg.region, content_type="text/csv")
    return f"s3://{bucket}/{key}"


def _wait_terminal(cfg: Config, job_name: str, *, timeout_seconds: int) -> dict[str, Any]:
    log(f"[wait] esperando estado terminal do training job {job_name} (timeout {timeout_seconds}s)")
    return aws.wait_for(
        lambda: aws.describe_training_job(job_name, cfg.region),
        lambda description: description["TrainingJobStatus"] in TRAINING_TERMINAL_STATES,
        timeout_seconds=timeout_seconds,
        description=f"training job {job_name} atingir estado terminal",
    )


def _write_training_evidence(cfg: Config, description: dict[str, Any]) -> None:
    status = description.get("TrainingJobStatus")
    record: dict[str, Any] = {
        "training_job_name": description.get("TrainingJobName"),
        "status": status,
        "secondary_status": description.get("SecondaryStatus"),
        "instance_type": description.get("ResourceConfig", {}).get("InstanceType"),
        "instance_count": description.get("ResourceConfig", {}).get("InstanceCount"),
        "volume_size_in_gb": description.get("ResourceConfig", {}).get("VolumeSizeInGB"),
        "max_runtime_in_seconds": description.get("StoppingCondition", {}).get("MaxRuntimeInSeconds"),
        "hyperparameters": description.get("HyperParameters", {}),
        "creation_time": _json_safe(description.get("CreationTime")),
        "training_start_time": _json_safe(description.get("TrainingStartTime")),
        "training_end_time": _json_safe(description.get("TrainingEndTime")),
        "billable_seconds": description.get("BillableTimeInSeconds"),
        "final_metrics": [
            {"name": metric.get("MetricName"), "value": metric.get("Value")}
            for metric in description.get("FinalMetricDataList", [])
        ],
    }
    if status == "Failed":
        record["failure_reason"] = description.get("FailureReason", "(nenhum FailureReason reportado pela API)")

    path = _evidence_path(cfg)
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# cmd_train
# --------------------------------------------------------------------------- #


def cmd_train(cfg: Config, args: Any) -> int:
    """Sobe train/validation para o S3 e cria o Training Job XGBoost via
    boto3. Com `args.wait` verdadeiro, espera o estado terminal antes de
    retornar (sem fazer o handoff do artefato — isso é `cmd_artifact`)."""
    try:
        identity = aws.check_credentials(cfg.region)
        bucket = _bucket_name(cfg, identity["account"])

        train_uri = _upload_channel(cfg, bucket, TRAIN_FILENAME)
        validation_uri = _upload_channel(cfg, bucket, VALIDATION_FILENAME)

        job_name = getattr(args, "job_name", None) or _job_name(cfg)
        output_uri = f"s3://{bucket}/{ARTIFACTS_PREFIX}/{job_name}/"
        role_arn = _role_arn(cfg)

        log(f"[train] criando training job {job_name} ({INSTANCE_TYPE}, imagem {TRAINING_IMAGE})")
        try:
            aws.client("sagemaker", cfg.region).create_training_job(
                TrainingJobName=job_name,
                HyperParameters=HYPERPARAMETERS,
                AlgorithmSpecification={
                    "TrainingImage": TRAINING_IMAGE,
                    "TrainingInputMode": "File",
                },
                RoleArn=role_arn,
                InputDataConfig=[
                    {
                        "ChannelName": "train",
                        "ContentType": "text/csv",
                        "InputMode": "File",
                        "DataSource": {
                            "S3DataSource": {
                                "S3DataType": "S3Prefix",
                                "S3Uri": train_uri,
                                "S3DataDistributionType": "FullyReplicated",
                            }
                        },
                    },
                    {
                        "ChannelName": "validation",
                        "ContentType": "text/csv",
                        "InputMode": "File",
                        "DataSource": {
                            "S3DataSource": {
                                "S3DataType": "S3Prefix",
                                "S3Uri": validation_uri,
                                "S3DataDistributionType": "FullyReplicated",
                            }
                        },
                    },
                ],
                OutputDataConfig={"S3OutputPath": output_uri},
                ResourceConfig={
                    "InstanceType": INSTANCE_TYPE,
                    "InstanceCount": 1,
                    "VolumeSizeInGB": VOLUME_SIZE_IN_GB,
                },
                StoppingCondition={"MaxRuntimeInSeconds": MAX_RUNTIME_IN_SECONDS},
                EnableNetworkIsolation=False,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ResourceLimitExceeded":
                raise aws.AwsError(
                    "ResourceLimitExceeded ao criar o training job: provavelmente já existe outro "
                    "job de treino em InProgress nesta conta do Academy. Rode "
                    "`aws sagemaker list-training-jobs --status-equals InProgress` e espere ele "
                    "terminar antes de tentar de novo."
                ) from exc
            raise aws.AwsError(f"falha ao criar o training job {job_name}: {exc}") from exc

        # Evidência inicial (status InProgress) já grava o nome do job, para
        # `training-status`/`artifact` acharem-no sem precisar de --job-name.
        _write_training_evidence(cfg, aws.describe_training_job(job_name, cfg.region))

        if not bool(getattr(args, "wait", False)):
            log(f"[train] job {job_name} criado, seguindo sem esperar (use --wait para bloquear)")
            emit({"training_job_name": job_name, "status": "InProgress"})
            return 0

        timeout_seconds = int(getattr(args, "timeout", DEFAULT_WAIT_TIMEOUT_SECONDS))
        description = _wait_terminal(cfg, job_name, timeout_seconds=timeout_seconds)
        status = description["TrainingJobStatus"]
        _write_training_evidence(cfg, description)

        if status != "Completed":
            reason = description.get("FailureReason", "(nenhum FailureReason reportado pela API)")
            log(f"[train] o training job {job_name} terminou como {status}: {reason}")
            emit({"training_job_name": job_name, "status": status, "failure_reason": reason})
            return 1

        log(f"[train] job {job_name} concluído ({description.get('BillableTimeInSeconds')}s cobrados)")
        emit({"training_job_name": job_name, "status": status})
        return 0
    except aws.AwsError as exc:
        log(f"[FAIL] {exc}")
        emit({"passed": False, "error": str(exc)})
        return 1


# --------------------------------------------------------------------------- #
# cmd_training_status
# --------------------------------------------------------------------------- #


def cmd_training_status(cfg: Config, args: Any) -> int:
    """`DescribeTrainingJob`, opcionalmente bloqueando até o estado terminal.

    Sem `--wait`: uma chamada avulsa, não bloqueia, não faz o handoff — só
    para acompanhar o progresso entre uma invocação e outra da CLI.

    Com `--wait` (usado pelo `make deploy`, que precisa saber se pode seguir
    para o estágio de serving): bloqueia via `aws.wait_for` até `Completed`,
    `Failed` ou `Stopped`, e o código de saída reflete isso — 0 só em
    `Completed`. Não faz o handoff do artefato; isso continua em
    `cmd_artifact`, que é quem escreve `.generated/artifact.auto.tfvars.json`.
    """
    try:
        job_name = _resolve_job_name(cfg, args)
        if bool(getattr(args, "wait", False)):
            timeout_seconds = int(getattr(args, "timeout", DEFAULT_WAIT_TIMEOUT_SECONDS))
            description = _wait_terminal(cfg, job_name, timeout_seconds=timeout_seconds)
        else:
            description = aws.describe_training_job(job_name, cfg.region)
        _write_training_evidence(cfg, description)

        status = description["TrainingJobStatus"]
        log(f"[training-status] {job_name}: {status} ({description.get('SecondaryStatus')})")

        payload: dict[str, Any] = {
            "training_job_name": job_name,
            "status": status,
            "secondary_status": description.get("SecondaryStatus"),
        }
        if status in ("Failed", "Stopped"):
            reason = description.get("FailureReason", "(nenhum FailureReason reportado pela API)")
            log(f"[training-status] motivo da falha: {reason}")
            payload["failure_reason"] = reason

        emit(payload)
        # Sem --wait, um status intermediário (InProgress/Stopping) não é erro —
        # só falha quando a API já reportou Failed. Com --wait, `_wait_terminal`
        # só devolve estado terminal, então "não Completed" aqui já é o estado
        # final do job, e o `make deploy` precisa desse != 0 para não seguir
        # para o serving com um treino que não terminou bem.
        if bool(getattr(args, "wait", False)):
            return 0 if status == "Completed" else 1
        return 0 if status != "Failed" else 1
    except aws.AwsError as exc:
        log(f"[FAIL] {exc}")
        emit({"passed": False, "error": str(exc)})
        return 1


# --------------------------------------------------------------------------- #
# cmd_artifact — o handoff
# --------------------------------------------------------------------------- #


def cmd_artifact(cfg: Config, args: Any) -> int:
    """Sequência obrigatória do handoff, na ordem exata do contrato:

    1. espera o estado terminal do training job (`aws.wait_for`);
    2. `DescribeTrainingJob` e lê `ModelArtifacts.S3ModelArtifacts`;
    3. `HeadObject` nessa URI, para provar que o objeto existe de fato;
    4. grava `.generated/artifact.auto.tfvars.json` para o estágio 2 do
       Terraform consumir;
    5. grava `artifacts/evidence/training.json` e `artifacts/evidence/artifact.json`.

    A URI nunca é montada por convenção de caminho — só o valor que a API
    devolveu chega a `model_artifact_s3_uri`. Se `HeadObject` falhar, esta
    função falha alto (exit != 0) em vez de seguir para o serving.
    """
    try:
        job_name = _resolve_job_name(cfg, args)
        timeout_seconds = int(getattr(args, "timeout", DEFAULT_WAIT_TIMEOUT_SECONDS))

        # 1. estado terminal.
        description = _wait_terminal(cfg, job_name, timeout_seconds=timeout_seconds)
        status = description["TrainingJobStatus"]
        _write_training_evidence(cfg, description)

        if status != "Completed":
            reason = description.get("FailureReason", "(nenhum FailureReason reportado pela API)")
            log(f"[artifact] o training job {job_name} terminou como {status}: {reason}")
            log("[artifact] recusando publicar um modelo — treino não terminou Completed")
            emit({"passed": False, "training_job_name": job_name, "status": status, "failure_reason": reason})
            return 1

        # 2. URI autoritativa, só da API.
        artifact_uri = description.get("ModelArtifacts", {}).get("S3ModelArtifacts", "")
        if not artifact_uri:
            raise aws.AwsError(
                f"o training job {job_name} terminou Completed, mas a API não reportou "
                "ModelArtifacts.S3ModelArtifacts — recusando publicar um modelo sem essa evidência."
            )

        # 3. prova de existência.
        bucket, key = _split_s3_uri(artifact_uri)
        log(f"[artifact] confirmando com HeadObject: s3://{bucket}/{key}")
        head = aws.head_object(bucket, key, cfg.region)
        content_length = int(head.get("ContentLength", 0))
        if content_length <= 0:
            raise aws.AwsError(f"o artefato {artifact_uri} existe mas está vazio — recusando publicar.")

        # 4. handoff para o estágio 2 do Terraform.
        handoff_path = _handoff_path(cfg)
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        handoff_path.write_text(
            json.dumps({"model_artifact_s3_uri": artifact_uri}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        log(f"[artifact] handoff escrito em {handoff_path}")

        # 5. evidência do artefato (a de treino já foi escrita no passo 1).
        etag = str(head.get("ETag", "")).strip('"')
        record = {
            "training_job_name": job_name,
            "model_artifact_s3_uri": artifact_uri,
            "content_length": content_length,
            "etag": etag,
            "checked_at": _utc_now_iso(),
        }
        _artifact_evidence_path(cfg).write_text(
            json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
        )

        log(f"[artifact] artefato confirmado: {content_length} bytes, etag {etag}")
        emit(record)
        return 0
    except aws.AwsError as exc:
        log(f"[FAIL] {exc}")
        emit({"passed": False, "error": str(exc)})
        return 1
