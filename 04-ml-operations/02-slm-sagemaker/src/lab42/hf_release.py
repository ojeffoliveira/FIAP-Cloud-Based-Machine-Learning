"""Supply chain do modelo: manifest, download verificado e release recomendada.

Lê e valida `model/releases/v{1,2}.yaml`, resolve a URL exata por revisão no
Hugging Face Hub, baixa com retomada/retry, calcula SHA-256 em streaming e
falha duro em qualquer mismatch. Publica no bucket de artifacts com metadata
(sha256/revision/release/quantization) na key imutável por release (layout
D7: um prefixo por release, um único `.gguf` por prefixo — nunca o layout
antigo que misturaria Q4_0 e Q4_K_M no mesmo prefixo que o SageMaker
`ModelDataSource` sincroniza inteiro para `/opt/ml/model`). Também implementa
o ponteiro `releases/recommended.json` (contrato em
`/tmp/cloud-code-lab04-2-a7/spec-recommended-json.md`).

Todo acesso à AWS passa por `lab42.aws` (client cacheado, credencial via
cadeia padrão do boto3) — este módulo nunca chama `boto3` direto. Toda
mensagem de erro acionável é `LabError`, em português, com o próximo passo.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml
from botocore.exceptions import ClientError

from . import aws
from .aws import LabError, log

RELEASES_DIR = aws.LAB_ROOT / "model" / "releases"

# Mesmo contrato validado estaticamente por tests/test_release_manifest.py —
# repetido aqui porque este módulo toca AWS de verdade e não pode confiar em
# schema não validado (o teste cobre o repositório, não o runtime).
REQUIRED_TOP_LEVEL_FIELDS = {
    "release",
    "model",
    "instance_type",
    "max_context",
    "max_output_tokens",
    "temperature",
    "prompt_contract_version",
}
REQUIRED_MODEL_FIELDS = {
    "source",
    "repo",
    "revision",
    "filename",
    "sha256",
    "size_bytes",
    "quantization",
    "license",
}
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

CHUNK_SIZE = 1024 * 1024  # 1 MiB — nunca carregar o arquivo inteiro em memória.
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 2

RECOMMENDED_KEY = "releases/recommended.json"

# --------------------------------------------------------------------------- #
# Manifest: caminho, leitura, validação de schema
# --------------------------------------------------------------------------- #


def manifest_path(release: str) -> Path:
    return RELEASES_DIR / f"{release}.yaml"


def load_manifest(path: Path) -> dict[str, Any]:
    """Lê e valida `model/releases/<release>.yaml`.

    Falha dura (LabError) em qualquer violação de schema — nunca segue
    adiante com um manifest incompleto ou com um placeholder de container
    não resolvido, porque o próximo passo é gastar banda/crédito AWS de
    verdade.
    """
    if not path.exists():
        raise LabError(
            f"manifest de release ausente: {path}. Ele é versionado no "
            "repositório (model/releases/), não gerado em tempo de execução."
        )
    with path.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    faltando_topo = REQUIRED_TOP_LEVEL_FIELDS - doc.keys()
    if faltando_topo:
        raise LabError(
            f"{path.name}: campos de topo ausentes no manifest: {sorted(faltando_topo)}"
        )

    faltando_model = REQUIRED_MODEL_FIELDS - doc.get("model", {}).keys()
    if faltando_model:
        raise LabError(
            f"{path.name}: campos de 'model' ausentes no manifest: {sorted(faltando_model)}"
        )

    revision = doc["model"]["revision"]
    if revision == "main" or not HEX40_RE.fullmatch(revision):
        raise LabError(
            f"{path.name}: model.revision deve ser um commit SHA completo de 40 "
            f"caracteres hex (nunca 'main'); encontrado {revision!r}. 'main' pode "
            "avançar entre a preparação da aula e a execução em sala — resolva a "
            "revisão real via `git ls-remote` ou `HfApi().repo_info()` antes de "
            "editar o manifest."
        )

    sha256 = doc["model"]["sha256"]
    if not HEX64_RE.fullmatch(sha256):
        raise LabError(
            f"{path.name}: model.sha256 deve ser hex de 64 caracteres; encontrado {sha256!r}"
        )

    if doc["model"]["size_bytes"] <= 0:
        raise LabError(
            f"{path.name}: model.size_bytes deve ser > 0; encontrado {doc['model']['size_bytes']!r}"
        )

    container = doc.get("container") or {}
    for campo in ("uri", "tag", "digest"):
        valor = container.get(campo)
        if valor is None or str(valor).strip() == "<<A2>>":
            raise LabError(
                f"{path.name}: container.{campo} ainda não foi resolvido (placeholder "
                "<<A2>> presente). Preencha container.uri/tag/digest com os valores "
                "reais confirmados no preflight de runtime antes do sync — não "
                "inventar URI/tag/digest aqui."
            )

    return doc


# --------------------------------------------------------------------------- #
# Layout S3 (D7 — um prefixo por release, um único .gguf por prefixo)
# --------------------------------------------------------------------------- #


def model_repo_dirname(repo: str) -> str:
    """'Qwen/Qwen2.5-0.5B-Instruct-GGUF' -> 'Qwen2.5-0.5B-Instruct-GGUF'.

    O prefixo S3 usa só o nome do modelo, sem o org do Hub — layout congelado
    em 09_IMPLEMENTATION_BLUEPRINT.md §3 e reafirmado pela decisão D7.
    """
    return repo.rsplit("/", 1)[-1]


def s3_prefix_for(manifest: dict[str, Any]) -> str:
    """Prefixo (com barra final) — é isso que o A6 aponta no `ModelDataSource`
    com `S3DataType=S3Prefix`: o SageMaker sincroniza o prefixo INTEIRO para
    `/opt/ml/model`, por isso cada release precisa do seu próprio prefixo com
    exatamente um `.gguf` dentro (D7)."""
    model = manifest["model"]
    dirname = model_repo_dirname(model["repo"])
    return f"models/{dirname}/{model['revision']}/{manifest['release']}/"


def s3_key_for(manifest: dict[str, Any]) -> str:
    return s3_prefix_for(manifest) + manifest["model"]["filename"]


def metadata_json_key(release: str) -> str:
    return f"metadata/{release}.json"


# --------------------------------------------------------------------------- #
# Download com retomada/retry + verificação de integridade
# --------------------------------------------------------------------------- #


def resolve_download_url(repo: str, revision: str, filename: str) -> str:
    return f"https://huggingface.co/{repo}/resolve/{revision}/{filename}"


def _download_with_retry(url: str, dest: Path, *, expected_size: int) -> None:
    """Baixa `url` para `dest`, com retomada via `Range` e retry/backoff.

    Só tenta de novo erro de rede/timeout/5xx ou tamanho final incompatível
    (queda no meio do stream) — um 4xx é falha imediata (indica revisão/URL
    errada, não instabilidade de rede) e propaga via `raise_for_status`.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        resume_from = dest.stat().st_size if dest.exists() else 0
        if resume_from > expected_size:
            dest.unlink()
            resume_from = 0
        if resume_from == expected_size:
            return

        log(
            f"download tentativa {attempt}/{MAX_ATTEMPTS}"
            + (f" (retomando de {resume_from} bytes)" if resume_from else "")
        )
        headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
        try:
            with requests.get(url, headers=headers, stream=True, timeout=30) as resp:
                if resume_from and resp.status_code == 200:
                    # Servidor ignorou o Range (não suporta retomada nesta
                    # resposta) — reinicia do zero para não corromper o arquivo.
                    resume_from = 0
                    dest.unlink(missing_ok=True)
                elif resp.status_code not in (200, 206):
                    resp.raise_for_status()
                modo = "ab" if resume_from else "wb"
                with dest.open(modo) as fh:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if chunk:
                            fh.write(chunk)
        except requests.exceptions.RequestException as exc:
            if attempt == MAX_ATTEMPTS:
                raise LabError(
                    f"download de {url} falhou após {MAX_ATTEMPTS} tentativas ({exc}). "
                    "Confira conectividade com huggingface.co:443 (`make doctor`) e "
                    "rode de novo — o download retoma do ponto onde parou."
                ) from exc
            espera = BACKOFF_BASE_S**attempt
            log(
                f"falha de rede na tentativa {attempt}: {exc}. Nova tentativa em {espera}s."
            )
            time.sleep(espera)
            continue

        tamanho_atual = dest.stat().st_size
        if tamanho_atual == expected_size:
            return
        if attempt == MAX_ATTEMPTS:
            raise LabError(
                f"download de {url} terminou com {tamanho_atual} bytes, esperado "
                f"{expected_size}. Possível queda de rede no meio do stream — apague "
                f"{dest} e rode de novo."
            )
        log(
            f"tamanho obtido ({tamanho_atual}) difere do esperado ({expected_size}); nova tentativa retomando."
        )

    raise LabError(f"download de {url} não completou após {MAX_ATTEMPTS} tentativas.")


def sha256_of_file(path: Path) -> str:
    """SHA-256 em streaming (blocos de 1 MiB) — o arquivo tem ~430-490 MB,
    nunca carregado inteiro em memória."""
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_integrity(
    path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    repo: str,
    filename: str,
    revision: str,
) -> str:
    """Confere tamanho e SHA-256; falha dura e didática em qualquer mismatch.

    Esse é o gate de segurança central do supply chain do modelo — ver
    `/tmp/cloud-code-lab04-2-a7/riscos-supply-chain.md`. Tamanho é checado
    primeiro porque é mais barato que o hash e já pega download truncado.
    """
    tamanho = path.stat().st_size
    if tamanho != expected_size:
        raise LabError(
            f"tamanho do arquivo baixado não bate: esperado {expected_size} bytes, "
            f"obtido {tamanho} bytes ({repo}/{filename} @ {revision})."
        )
    log(f"calculando SHA-256 de {path.name} ({tamanho} bytes)...")
    sha_local = sha256_of_file(path)
    if sha_local.lower() != expected_sha256.lower():
        raise LabError(
            "SHA-256 do arquivo baixado não bate com o manifest.\n"
            f"  manifest:  {expected_sha256}\n"
            f"  calculado: {sha_local}\n"
            f"  arquivo:   {repo}/{filename} @ {revision}\n"
            "Isso significa que o Hub mudou o conteúdo do arquivo nesta revisão "
            "(não deveria acontecer com um commit SHA imutável) ou que o download "
            "foi corrompido. Não prossiga: apague o arquivo local e rode de novo. "
            "Se o mismatch persistir, trate como incidente de supply chain, não "
            "como bug de rede."
        )
    log("integridade confirmada: SHA-256 bate com o manifest")
    return sha_local


def download_and_verify(manifest: dict[str, Any]) -> Path:
    """Baixa o `.gguf` da revisão pinada para um diretório temporário FORA do
    repositório e confirma a integridade. O chamador é responsável por
    remover o arquivo (e o diretório) ao final — nunca fica em `/tmp` entre
    execuções, nunca entra em `artifacts/` nem em caminho versionado."""
    model = manifest["model"]
    url = resolve_download_url(model["repo"], model["revision"], model["filename"])
    tmp_dir = Path(tempfile.mkdtemp(prefix="lab42-model-"))
    dest = tmp_dir / model["filename"]
    log(f"URL resolvida: {url}")
    _download_with_retry(url, dest, expected_size=model["size_bytes"])
    verify_integrity(
        dest,
        expected_sha256=model["sha256"],
        expected_size=model["size_bytes"],
        repo=model["repo"],
        filename=model["filename"],
        revision=model["revision"],
    )
    return dest


# --------------------------------------------------------------------------- #
# S3 — idempotência (head-object primeiro), upload com metadata, metadata/*.json
# --------------------------------------------------------------------------- #


def head_object_matches(
    bucket: str, key: str, *, expected_sha256: str, expected_size: int
) -> bool:
    """True se o objeto já existe no S3 com o SHA-256/tamanho corretos —
    nesse caso o chamador pula download+upload (evita re-subir ~500 MB em
    aula). Falha dura se existir com SHA divergente: a key é imutável por
    release/revisão, então um SHA diferente sob a mesma key é inconsistência
    grave, nunca "atualização normal"."""
    head = aws.head_object(bucket, key)
    if head is None:
        return False
    metadata_sha = (head.get("Metadata") or {}).get("sha256", "")
    tamanho = head.get("ContentLength")
    if metadata_sha and metadata_sha.lower() != expected_sha256.lower():
        raise LabError(
            f"objeto já existe em s3://{bucket}/{key} mas com sha256 divergente do "
            f"manifest (metadata={metadata_sha!r}, manifest={expected_sha256!r}). A key "
            "é imutável por release/revisão — isso é inconsistência grave, não "
            "atualização normal. Investigue antes de continuar; o objeto não foi "
            "sobrescrito."
        )
    return bool(metadata_sha) and tamanho == expected_size


def upload_with_metadata(
    local_path: Path, bucket: str, key: str, *, metadata: dict[str, str]
) -> dict[str, Any]:
    log(
        f"enviando {local_path.name} para s3://{bucket}/{key} ({local_path.stat().st_size} bytes)..."
    )
    aws.client("s3").upload_file(
        str(local_path),
        bucket,
        key,
        ExtraArgs={"ServerSideEncryption": "AES256", "Metadata": metadata},
    )
    head = aws.head_object(bucket, key)
    if head is None:
        raise LabError(
            f"upload para s3://{bucket}/{key} não pôde ser confirmado por head-object depois do put."
        )
    log(f"upload confirmado por head-object: s3://{bucket}/{key}")
    return head


def write_s3_metadata_json(bucket: str, release: str, payload: dict[str, Any]) -> None:
    key = metadata_json_key(release)
    body = json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode(
        "utf-8"
    )
    aws.client("s3").put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    log(f"metadata gravado em s3://{bucket}/{key}")


def read_s3_metadata_json(bucket: str, release: str) -> dict[str, Any] | None:
    try:
        obj = aws.client("s3").get_object(Bucket=bucket, Key=metadata_json_key(release))
    except ClientError:
        return None
    return json.loads(obj["Body"].read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# releases/recommended.json — ponteiro móvel de release recomendada
# --------------------------------------------------------------------------- #


def read_recommended_release(bucket: str) -> dict[str, Any] | None:
    try:
        obj = aws.client("s3").get_object(Bucket=bucket, Key=RECOMMENDED_KEY)
    except ClientError:
        return None
    return json.loads(obj["Body"].read().decode("utf-8"))


def write_recommended_release(
    bucket: str,
    release: str,
    *,
    reason: str,
    evidence_refs: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Grava `releases/recommended.json` — só depois que os gates da release
    (smoke, evaluation, autoscaling) já passaram (contrato em
    `/tmp/cloud-code-lab04-2-a7/spec-recommended-json.md`). Este módulo não
    decide SE deve promover — só materializa a promoção decidida pelo
    chamador (o pipeline V2, depois dos gates)."""
    if release not in {"v1", "v2"}:
        raise LabError(
            f"recommended_release deve ser 'v1' ou 'v2'; recebido {release!r}"
        )

    anterior = read_recommended_release(bucket)
    payload = {
        "recommended_release": release,
        "reason": reason,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "promoted_by": {
            "method": "github-actions-self-hosted"
            if os.environ.get("GITHUB_ACTIONS")
            else "manual",
            "workflow": os.environ.get("GITHUB_WORKFLOW", "manual"),
            "run_id": os.environ.get("GITHUB_RUN_ID", "n/a"),
            "actor": os.environ.get("GITHUB_ACTOR", "n/a"),
            "commit": os.environ.get("GITHUB_SHA", "n/a"),
        },
        "evidence_refs": evidence_refs or {},
        "previous_recommended_release": (anterior or {}).get("recommended_release"),
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    aws.client("s3").put_object(
        Bucket=bucket,
        Key=RECOMMENDED_KEY,
        Body=body,
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    log(
        f"releases/recommended.json atualizado: recommended_release={release!r} ({reason})"
    )
    return payload
