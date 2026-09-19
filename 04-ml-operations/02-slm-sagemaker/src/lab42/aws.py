"""Fronteira com a AWS — clientes boto3, config do lab e outputs do Terraform.

Todo acesso à AWS do Lab 04.2 passa por aqui, incluindo os módulos dos outros
agentes (`hf_release.py`, `inference.py`, `evaluation.py`): nenhum outro
arquivo do pacote chama `boto3` ou lê `config/lab.yaml` diretamente. Isso
mantém uma única leitura cacheada do config e um único lugar para tratar
credencial ausente/expirada.

Disciplina de saída, valendo para o lab inteiro: **stdout carrega resultado**
(o JSON que alguém vai capturar ou pipar) e **stderr carrega progresso**. A
função `log()` é a única forma correta de narrar progresso em qualquer script
do lab.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import subprocess  # nosec - só invoca binários fixos (git/terraform) via argv em lista, nunca shell=True
import sys
from pathlib import Path
from typing import Any

import boto3
import yaml
from botocore.config import Config
from botocore.exceptions import ClientError, NoCredentialsError, TokenRetrievalError

# --------------------------------------------------------------------------- #
# Caminhos e config
# --------------------------------------------------------------------------- #

LAB_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = LAB_ROOT / "config" / "lab.yaml"
ARTIFACTS_DIR = LAB_ROOT / "artifacts"
EVIDENCE_DIR = ARTIFACTS_DIR / "evidence"
GENERATED_DIR = LAB_ROOT / ".generated"
TERRAFORM_SLM_DIR = LAB_ROOT / "terraform" / "slm"
TERRAFORM_RUNNER_DIR = LAB_ROOT / "terraform" / "runner"


class LabError(RuntimeError):
    """Falha acionável do laboratório: a mensagem diz ao aluno o que fazer."""


def log(*args: Any) -> None:
    """Progresso para stderr. Nunca imprime credencial, token ou secret."""
    print(*args, file=sys.stderr, flush=True)


def emit(payload: Any) -> None:
    """Resultado para stdout, sempre como JSON."""
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


@functools.lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Devolve o config/lab.yaml já parseado. Cacheado: é lido muitas vezes."""
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def ensure_dirs() -> None:
    for path in (ARTIFACTS_DIR, EVIDENCE_DIR, GENERATED_DIR):
        path.mkdir(parents=True, exist_ok=True)


def region() -> str:
    return str(load_config()["region"])


# --------------------------------------------------------------------------- #
# Sessão e clientes boto3
# --------------------------------------------------------------------------- #

# O retry padrão do boto3 é tímido para o volume de chamadas curtas que o lab
# faz em sequência (doctor, verify-clean, benchmark). O modo adaptativo
# respeita throttling sem o aluno ver erro.
_BOTO_CONFIG = Config(retries={"max_attempts": 8, "mode": "adaptive"})


@functools.cache
def client(service: str) -> Any:
    """Client boto3 cacheado por serviço/processo.

    Não passa `profile_name` nem `aws_access_key_id`: a sessão usa a cadeia
    padrão de resolução de credencial do boto3, que já cobre os dois ambientes
    do lab sem `if` de ambiente — variável `AWS_PROFILE` local (Codespaces com
    `~/.aws/credentials` colado do Academy) e instance profile do EC2 runner
    (IMDSv2, sem qualquer chave estática) — ver 04_AWS_ACADEMY_GUARDRAILS.md.
    """
    return boto3.client(service, region_name=region(), config=_BOTO_CONFIG)


def session() -> boto3.Session:
    """Sessão boto3 crua, para quem precisa de algo alem de um client (ex.: credenciais)."""
    return boto3.Session(region_name=region())


# --------------------------------------------------------------------------- #
# Identidade e pré-condições
# --------------------------------------------------------------------------- #


def caller_identity() -> dict[str, str]:
    # NoCredentialsError e TokenRetrievalError derivam de BotoCoreError, não de
    # ClientError: sem capturá-las ANTES do except ClientError, o aluno sem
    # credencial colada veria o traceback do botocore no lugar do [FAIL]
    # (mesma técnica do commit 8e1f874 do Lab 04.1).
    try:
        return client("sts").get_caller_identity()
    except (NoCredentialsError, TokenRetrievalError) as exc:
        raise LabError(
            "não há credencial da AWS utilizável neste ambiente. Abra o painel do lab "
            "no AWS Academy (Learner Lab -> AWS Details -> AWS CLI) e cole o conteúdo "
            "em ~/.aws/credentials dentro do Codespaces. A credencial expira a cada "
            "4 horas; se já tinha colado antes, cole de novo."
        ) from exc
    except ClientError as exc:
        raise LabError(
            "credencial da AWS inválida ou expirada. No AWS Academy, abra o painel do "
            "lab e copie as credenciais novamente para ~/.aws/credentials."
        ) from exc


def account_id() -> str:
    """Account id de 12 dígitos, resolvido via STS — nunca digitado à mão."""
    return str(caller_identity()["Account"])


def role_arn(role_name: str) -> str:
    try:
        return client("iam").get_role(RoleName=role_name)["Role"]["Arn"]
    except (NoCredentialsError, TokenRetrievalError) as exc:
        raise LabError(
            "não há credencial da AWS utilizável neste ambiente. Abra o painel do lab "
            "no AWS Academy e cole as credenciais em ~/.aws/credentials."
        ) from exc
    except ClientError as exc:
        raise LabError(
            f"não foi possível ler a role {role_name!r}. Ela é criada pelo próprio "
            "Academy; se não existe, o lab foi aberto em uma conta diferente."
        ) from exc


def instance_profile_arn(profile_name: str) -> str:
    try:
        return client("iam").get_instance_profile(InstanceProfileName=profile_name)[
            "InstanceProfile"
        ]["Arn"]
    except (NoCredentialsError, TokenRetrievalError) as exc:
        raise LabError(
            "não há credencial da AWS utilizável neste ambiente. Abra o painel do lab "
            "no AWS Academy e cole as credenciais em ~/.aws/credentials."
        ) from exc
    except ClientError as exc:
        raise LabError(
            f"não foi possível ler o instance profile {profile_name!r}. Ele é criado "
            "pelo próprio Academy; se não existe, o lab foi aberto em uma conta diferente."
        ) from exc


# --------------------------------------------------------------------------- #
# Sufixo determinístico (blueprint §1) — nunca nome de aluno
# --------------------------------------------------------------------------- #


def github_owner() -> str:
    """Owner do repositório GitHub, para o sufixo determinístico.

    Ordem de resolução, sem fallback para nome de aluno (regra dura da spec):
    1. `GITHUB_REPOSITORY_OWNER`, presente automaticamente em qualquer job do
       GitHub Actions (inclusive o runner self-hosted do deploy V2);
    2. `remote.origin.url` do próprio checkout, parseado (Codespaces e
       execução local têm o fork clonado com esse remote configurado).
    """
    env_owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
    if env_owner:
        return env_owner

    # git é resolvido via PATH de propósito (funciona em Codespaces, local e no
    # runner self-hosted, onde o binário mora em diretórios diferentes); argv
    # é lista fixa sem input do usuário, shell=False (padrão) — os alertas de
    # shell injection do bandit não se aplicam a este uso.
    result = subprocess.run(  # nosec - lista fixa, sem shell, sem input do usuário (ver comentário acima)
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        check=False,
        cwd=LAB_ROOT,
    )
    url = result.stdout.strip()
    if result.returncode != 0 or not url:
        raise LabError(
            "não foi possível descobrir o owner do repositório GitHub (nem "
            "GITHUB_REPOSITORY_OWNER, nem remote.origin.url configurado). O sufixo "
            "determinístico dos recursos depende disso — confira se o repositório foi "
            "clonado a partir de um fork, e não copiado sem o remote git."
        )

    # Cobre as duas formas usuais: git@github.com:owner/repo.git e
    # https://github.com/owner/repo.git
    cleaned = url.rstrip("/").removesuffix(".git")
    tail = (
        cleaned.split(":")[-1]
        if cleaned.startswith("git@")
        else cleaned.split("/", 3)[-2:]
    )
    if isinstance(tail, list):
        if len(tail) < 2:
            raise LabError(f"remote.origin.url em formato inesperado: {url!r}")
        owner = tail[0]
    else:
        parts = tail.split("/")
        if len(parts) < 2:
            raise LabError(f"remote.origin.url em formato inesperado: {url!r}")
        owner = parts[0]

    if not owner:
        raise LabError(
            f"não foi possível extrair o owner de remote.origin.url: {url!r}"
        )
    return owner


@functools.lru_cache(maxsize=1)
def suffix() -> str:
    """Sufixo curto e determinístico: sha256(account_id + ':' + github_owner)[:8].

    Determinístico e reproduzível por qualquer aluno/CI que rode na mesma conta
    e no mesmo fork — nunca nome de aluno (proibido pelo blueprint §1). Cacheado
    porque `account_id()` chama STS.
    """
    raw = f"{account_id()}:{github_owner()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def resource_name(template: str) -> str:
    """Substitui `<account>`, `<hash8>` e `<suffix>` por valores resolvidos de verdade.

    Único ponto de expansão dos templates de `config/lab.yaml["naming"]` — nenhum
    script recalcula o sufixo com sua própria fórmula.
    """
    return (
        template.replace("<account>", account_id())
        .replace("<hash8>", suffix())
        .replace("<suffix>", suffix())
    )


def lab_tags() -> dict[str, str]:
    """Tags padrão do lab, para quem precisa tagueá-las fora do Terraform (ex.: CLI)."""
    return dict(load_config().get("tags", {}))


# --------------------------------------------------------------------------- #
# Outputs do Terraform
# --------------------------------------------------------------------------- #


def _terraform_binary() -> str:
    """Respeita o TF exportado pelo Makefile; cai no PATH quando ausente."""
    return os.environ.get("TF") or "terraform"


def terraform_outputs(chdir: Path) -> dict[str, Any]:
    """Lê os outputs de um state específico (`terraform/slm` ou `terraform/runner`).

    Não cacheia entre chamadas com `chdir` diferente — o lab tem dois stacks
    Terraform independentes (regra dura da spec §13), e um cache único
    misturaria os outputs de um com o outro.
    """
    # _terraform_binary() honra TF (Makefile) ou cai no PATH; diretório vem de
    # `chdir`, que é sempre um dos dois stacks fixos do lab (terraform/slm ou
    # terraform/runner), nunca input externo — mesma justificativa do
    # comentário em github_owner() acima.
    result = subprocess.run(  # nosec - lista fixa, sem shell, sem input externo (ver comentário acima)
        [_terraform_binary(), f"-chdir={chdir}", "output", "-json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise LabError(
            f"não foi possível ler os outputs do Terraform em {chdir}. Rode o apply "
            f"correspondente antes deste comando.\nsaída do terraform: {result.stderr.strip()}"
        )
    raw = json.loads(result.stdout or "{}")
    return {key: value.get("value") for key, value in raw.items()}


# --------------------------------------------------------------------------- #
# CloudWatch
# --------------------------------------------------------------------------- #


def get_dashboard(name: str) -> dict[str, Any]:
    """GetDashboard de verdade — nunca imprimir um link sem confirmar que o corpo existe.

    Um `dashboard_url` construído só a partir do nome abre 404 se o `apply` ainda
    não rodou ou se o painel foi apagado por fora do Terraform; esta chamada é o
    que evita entregar esse link quebrado ao aluno (spec-visual §2).
    """
    try:
        return client("cloudwatch").get_dashboard(DashboardName=name)
    except ClientError as exc:
        raise LabError(
            f"o painel {name!r} não existe no CloudWatch. Rode `make deploy-v1` "
            "primeiro — o dashboard sobe junto com o endpoint V1."
        ) from exc


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
            f"o endpoint {endpoint_name} está {status}, não InService. Espere o "
            "provisionamento terminar antes de invocar."
        )


# --------------------------------------------------------------------------- #
# S3
# --------------------------------------------------------------------------- #


def bucket_exists(bucket: str) -> bool:
    try:
        client("s3").head_bucket(Bucket=bucket)
        return True
    except ClientError:
        return False


def head_object(bucket: str, key: str) -> dict[str, Any] | None:
    """Prova que um objeto do S3 existe de fato — nunca assume caminho "na fé"."""
    try:
        return client("s3").head_object(Bucket=bucket, Key=key)
    except ClientError:
        return None


def list_objects(bucket: str, prefix: str) -> list[dict[str, Any]]:
    paginator = client("s3").get_paginator("list_objects_v2")
    found: list[dict[str, Any]] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        found.extend(page.get("Contents", []))
    return found
