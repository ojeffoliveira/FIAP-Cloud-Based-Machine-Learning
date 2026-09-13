#!/usr/bin/env python3
"""Bootstrap idempotente do bucket de artifacts do SLM (stack `terraform/slm`).

Mesma razão de existir do `bootstrap_backend.py` (bucket de state): o Terraform
não pode criar o próprio bucket nesta conta (D2 — a SCP do AWS Academy nega
`GetBucketObjectLockConfiguration`, que o provider `aws_s3_bucket` lê logo
depois do `CreateBucket`). Aqui a chamada é direto via boto3 (mesma chamada
que `aws s3api create-bucket` faria), sem o provider no meio — então o
problema não existe.

D23 (coordenador): a criação por API é urgente porque quatro agentes
(A6b/A7 e, antes de D24, A5/A6a) ficaram bloqueados esperando este bucket
existir para poder trabalhar. O Terraform (`terraform/slm/s3.tf`, recurso
`terraform_data.artifacts_bucket`) adota o bucket depois — o guard
`head-bucket` dos dois lados do `local-exec` já é idempotente por
construção, então rodar este script antes do primeiro `terraform apply` não
é contorno, é a mesma tática de bootstrap do backend aplicada de novo.

Nome determinístico: `fiap-cbml-42-models-<account>-<hash8>`
(config/lab.yaml -> naming.bucket_artifacts).

Idempotente: rodar de novo não recria o bucket nem falha se ele já existir.

Uso: python scripts/bootstrap_artifacts_bucket.py
Saída: stdout = JSON (bucket, region, created_now); stderr = progresso.
Exit code: 0 em sucesso, 1 em LabError (mensagem acionável, sem traceback).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from botocore.exceptions import ClientError

from lab42 import aws
from lab42.aws import LabError, emit, log


def _naming() -> dict[str, str]:
    naming = aws.load_config().get("naming")
    if not naming:
        raise LabError(
            "config/lab.yaml não tem a seção 'naming' — sem ela não há como derivar "
            "o nome do bucket de artifacts de forma determinística."
        )
    return naming


def artifacts_bucket_name() -> str:
    """`fiap-cbml-42-models-<account>-<hash8>` com os placeholders resolvidos."""
    return aws.resource_name(_naming()["bucket_artifacts"])


def _create_bucket(s3: object, bucket: str, region: str) -> None:
    create_kwargs: dict = {"Bucket": bucket}
    # us-east-1 é a única região que a conta do Academy permite (config/lab.yaml)
    # e a API do S3 rejeita CreateBucketConfiguration explícita para ela.
    if region != "us-east-1":
        create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3.create_bucket(**create_kwargs)
        s3.get_waiter("bucket_exists").wait(Bucket=bucket)
    except s3.exceptions.BucketAlreadyOwnedByYou:
        # Corrida com outra chamada (vários agentes tentando destravar ao
        # mesmo tempo) — o bucket já é nosso, segue como se tivesse
        # encontrado pronto.
        log(f"[PASS] bucket já pertencia a esta conta (corrida evitada): {bucket}")
        return
    except ClientError as exc:
        raise LabError(
            f"não foi possível criar o bucket de artifacts {bucket!r}. Confira se a "
            "credencial do Academy está válida (~/.aws/credentials) e se a região "
            f"é us-east-1.\nerro da AWS: {exc}"
        ) from exc


def _configure_bucket(s3: object, bucket: str) -> None:
    s3.put_bucket_versioning(
        Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
    )
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        },
    )
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    try:
        s3.put_bucket_tagging(
            Bucket=bucket,
            Tagging={
                "TagSet": [{"Key": k, "Value": v} for k, v in aws.lab_tags().items()]
            },
        )
    except ClientError as exc:
        # Mesma tolerância documentada em s3.tf: um bucket sem tag é um
        # desfecho muito melhor que reprovar o bootstrap por uma SCP que
        # nega PutBucketTagging.
        log(f"aviso: não foi possível taguear o bucket de artifacts (seguindo): {exc}")


def ensure_artifacts_bucket(bucket: str, region: str) -> bool:
    """Garante que o bucket de artifacts existe e está configurado. Retorna True se criou agora."""
    s3 = aws.client("s3")
    if aws.bucket_exists(bucket):
        log(f"[PASS] bucket de artifacts já existe: {bucket}")
        return False

    log(f"criando bucket de artifacts: {bucket} (região {region})")
    _create_bucket(s3, bucket, region)
    _configure_bucket(s3, bucket)
    log(
        f"[PASS] bucket de artifacts criado e configurado (versioning+encryption+PAB): {bucket}"
    )
    return True


def main() -> int:
    region = aws.region()
    log(
        f"conta: {aws.account_id()} | owner github: {aws.github_owner()} | suffix: {aws.suffix()}"
    )

    bucket = artifacts_bucket_name()
    created_now = ensure_artifacts_bucket(bucket, region)

    emit(
        {
            "bucket": bucket,
            "region": region,
            "created_now": created_now,
        }
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LabError as exc:
        log(f"[FAIL] {exc}")
        sys.exit(1)
