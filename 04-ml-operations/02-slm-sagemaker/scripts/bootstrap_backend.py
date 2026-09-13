#!/usr/bin/env python3
"""Bootstrap idempotente do backend S3 do Terraform (stack `terraform/slm`).

O Terraform não cria o próprio backend: o bucket de state precisa existir
*antes* do primeiro `terraform init`. Este script:

1. deriva o nome do bucket de forma determinística — mesma fórmula usada por
   `lab42.aws.suffix()` e documentada em `config/lab.yaml` (naming.suffix_algorithm):
   `sha256(f"{account_id}:{github_owner}")[:8]`. `account_id` vem de
   `sts.get_caller_identity()` e `github_owner` de `GITHUB_REPOSITORY_OWNER`
   (presente em qualquer job do GitHub Actions, incluindo o runner
   self-hosted efêmero do deploy V2) ou, na ausência dela, de
   `git config --get remote.origin.url` do próprio checkout. Isso é o que
   permite a MESMA derivação funcionar no Codespaces e no runner EC2 sem
   nenhum secret e sem copiar/colar nada entre os dois ambientes;
2. cria o bucket se ainda não existir, com versioning, encryption (AES256) e
   block public access — nunca via `aws_s3_bucket` do Terraform (D2: a SCP do
   Academy nega `GetBucketObjectLockConfiguration`, que o provider lê logo
   depois do `CreateBucket`). Aqui não há esse problema porque é a API do S3
   chamada direto via boto3 (mesma chamada que `aws s3api create-bucket`
   faria), sem o provider AWS no meio;
3. escreve `.generated/backend.hcl`, consumido por
   `terraform -chdir=terraform/slm init -backend-config=.generated/backend.hcl`
   (backend parcial em `terraform/slm/backend.tf` — um backend block não
   aceita variável nem interpolação, por isso o bucket nunca é hardcoded lá).

Idempotente: rodar de novo não recria o bucket nem falha se ele já existir.

Uso: python scripts/bootstrap_backend.py
Saída: stdout = JSON (bucket, key, region, se criou agora); stderr = progresso.
Exit code: 0 em sucesso, 1 em LabError (mensagem acionável, sem traceback).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from botocore.exceptions import ClientError  # noqa: E402

from lab42 import aws  # noqa: E402
from lab42.aws import LabError, emit, log  # noqa: E402


def _naming() -> dict[str, str]:
    naming = aws.load_config().get("naming")
    if not naming:
        raise LabError(
            "config/lab.yaml não tem a seção 'naming' — sem ela não há como derivar "
            "o nome do bucket de state de forma determinística."
        )
    return naming


def state_bucket_name() -> str:
    """`fiap-cbml-42-tfstate-<account>-<hash8>` com os placeholders resolvidos."""
    return aws.resource_name(_naming()["bucket_state"])


def state_bucket_key() -> str:
    return str(_naming()["bucket_state_key"])


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
        # Corrida com outra chamada (ex.: dois `make backend` em paralelo) —
        # o bucket já é nosso, segue como se tivesse encontrado pronto.
        log(f"[PASS] bucket já pertencia a esta conta (corrida evitada): {bucket}")
        return
    except ClientError as exc:
        raise LabError(
            f"não foi possível criar o bucket de state {bucket!r}. Confira se a "
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
        # Mesma tolerância documentada em s3.tf do Lab 04.1: um bucket sem tag
        # é um desfecho muito melhor que reprovar o bootstrap por uma SCP que
        # nega PutBucketTagging.
        log(f"aviso: não foi possível taguear o bucket de state (seguindo): {exc}")


def ensure_state_bucket(bucket: str, region: str) -> bool:
    """Garante que o bucket de state existe e está configurado. Retorna True se criou agora."""
    s3 = aws.client("s3")
    if aws.bucket_exists(bucket):
        log(f"[PASS] bucket de state já existe: {bucket}")
        return False

    log(f"criando bucket de state: {bucket} (região {region})")
    _create_bucket(s3, bucket, region)
    _configure_bucket(s3, bucket)
    log(
        f"[PASS] bucket de state criado e configurado (versioning+encryption+PAB): {bucket}"
    )
    return True


def write_backend_hcl(bucket: str, key: str, region: str) -> Path:
    aws.ensure_dirs()
    path = aws.GENERATED_DIR / "backend.hcl"
    path.write_text(
        "# Gerado por scripts/bootstrap_backend.py — nunca editar à mão, nunca commitar.\n"
        f'bucket       = "{bucket}"\n'
        f'key          = "{key}"\n'
        f'region       = "{region}"\n'
        "use_lockfile = true\n"
        "encrypt      = true\n",
        encoding="utf-8",
    )
    log(f"[PASS] {path} escrito")
    return path


def main() -> int:
    region = aws.region()
    log(
        f"conta: {aws.account_id()} | owner github: {aws.github_owner()} | suffix: {aws.suffix()}"
    )

    bucket = state_bucket_name()
    key = state_bucket_key()

    created_now = ensure_state_bucket(bucket, region)
    backend_path = write_backend_hcl(bucket, key, region)

    emit(
        {
            "bucket": bucket,
            "key": key,
            "region": region,
            "use_lockfile": True,
            "created_now": created_now,
            "backend_hcl": str(backend_path),
            # Com `-chdir=terraform/slm`, o path de -backend-config resolve
            # relativo ao DIRETÓRIO DE DESTINO do -chdir, não ao cwd original
            # (confirmado empiricamente nesta conta — comportamento diferente
            # do que a documentação do -chdir sugere para outras flags como
            # -var-file). Por isso "../../" antes de ".generated/backend.hcl".
            "next_step": (
                "terraform -chdir=terraform/slm init -backend-config=../../.generated/backend.hcl"
            ),
        }
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LabError as exc:
        log(f"[FAIL] {exc}")
        sys.exit(1)
