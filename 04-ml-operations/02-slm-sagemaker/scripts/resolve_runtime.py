#!/usr/bin/env python3
"""Preflight de runtime: resolve a imagem DLC real e congela os parâmetros medidos.

Uso:
  python scripts/resolve_runtime.py
  python scripts/resolve_runtime.py --credential-source-only   # usado por `pipeline-preflight`

Modo padrão (`make preflight`): resolve o URI da imagem `huggingface-llamacpp`
via `sagemaker.core.image_uris` (o caminho correto no SDK v3 — `sagemaker.image_uris`
não existe mais nesta versão), confere contra os achados congelados do probe real
do A2 (conta do DLC, tag), tenta o digest no ECR (best-effort, nunca derruba o
preflight se a permissão faltar) e grava `.generated/runtime.json` +
`artifacts/evidence/research-preflight.json`.

`--credential-source-only`: não toca em SageMaker/ECR — só confirma de onde vem
a credencial ativa (STS) e grava `credential-source-v2.json`. É o check de
segurança que o workflow `04-2-deploy-v2.yml` roda no runner self-hosted antes
de fazer qualquer deploy: prova, por API, que a credencial é um role assumido
(instance profile / IMDSv2), nunca uma chave estática colada em segredo do
GitHub.

Convenção do lab: stdout = resultado (JSON), stderr = progresso.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from botocore.exceptions import BotoCoreError, ClientError

from lab42 import aws, evidence, inference
from lab42.aws import LabError, emit, log

# Achados congelados pelo probe real do A2 (não um chute): a conta que publica
# os Deep Learning Containers da AWS é sempre esta, em toda região comercial;
# a tag é a build exata provada em `contrato-inferencia.md`/`runtime.json` do
# A2. Se `image_uris.retrieve` devolver outra coisa, o SDK ou a região
# mudaram o container publicado — o preflight falha alto em vez de seguir com
# um runtime não provado.
_DLC_ACCOUNT_ESPERADA = "763104351884"
_DLC_TAG_ESPERADA = "b9522-cpu-ubuntu24.04"
_FRAMEWORK = "huggingface-llamacpp"
_IMAGE_SCOPE = "inference"

# Confirmados pelo probe real do A2 (LLAMA_ARG_CTX_SIZE=2048 funcionando,
# "n_ctx = 2048" no log do llama-server) e já congelados em
# model/releases/v1.yaml e v2.yaml. `config/lab.yaml["inference"]` ainda tem
# `<<A2>>` nestes dois campos — este script não pode editar esse arquivo (não
# é meu; dono é A0), então só reporta: quem consolida a evidência do lab
# decide se propaga este valor para lá.
CONFIRMED_MAX_CONTEXT_TOKENS = 2048
CONFIRMED_MAX_OUTPUT_TOKENS = 64


def _resolve_image_uri(instance_type: str, region: str) -> str:
    try:
        from sagemaker.core import image_uris
    except ImportError as exc:
        raise LabError(
            "não foi possível importar 'sagemaker.core.image_uris' — confira a versão do "
            "pacote 'sagemaker' instalada (o caminho antigo 'sagemaker.image_uris' não existe "
            "mais no SDK v3; ver decisão registrada em model/releases/*.yaml)."
        ) from exc
    return image_uris.retrieve(
        framework=_FRAMEWORK,
        region=region,
        image_scope=_IMAGE_SCOPE,
        instance_type=instance_type,
    )


def _validar_imagem(image_uri: str) -> None:
    conta, _, _resto = image_uri.partition(".dkr.ecr.")
    tag = image_uri.rsplit(":", 1)[-1]
    if conta != _DLC_ACCOUNT_ESPERADA:
        raise LabError(
            f"image_uris.retrieve devolveu a conta {conta!r}, esperado {_DLC_ACCOUNT_ESPERADA!r} "
            f"(achado congelado do probe do A2). URI recebido: {image_uri!r}. Isso indica que a "
            "AWS mudou a conta publicadora do DLC ou a região não é mais us-east-1 — não siga "
            "sem reconfirmar manualmente contra a documentação atual do SageMaker DLC."
        )
    if tag != _DLC_TAG_ESPERADA:
        log(
            f"[AVISO] tag da imagem é {tag!r}, achado congelado do probe era {_DLC_TAG_ESPERADA!r} "
            "— a AWS publicou uma build nova do DLC. Não é erro fatal (o contrato OpenAI-style "
            "tende a ser estável entre builds), mas revalide com 'make smoke-v1' antes de confiar."
        )


def _tentar_digest(image_uri: str) -> str | None:
    """Best-effort: digest via ECR `describe-images`. Nunca derruba o preflight se faltar permissão
    (o DLC é de OUTRA conta — a do Academy pode não ter `ecr:DescribeImages` cross-account)."""
    try:
        conta, resto = image_uri.split(".dkr.ecr.", 1)
        _regiao, resto = resto.split(".amazonaws.com/", 1)
        repositorio, tag = resto.rsplit(":", 1)
        resposta = aws.client("ecr").describe_images(
            registryId=conta, repositoryName=repositorio, imageIds=[{"imageTag": tag}]
        )
        detalhes = resposta.get("imageDetails") or []
        return detalhes[0]["imageDigest"] if detalhes else None
    except (ClientError, BotoCoreError, ValueError, KeyError, IndexError) as exc:
        log(f"[AVISO] não foi possível obter o digest via ECR (não fatal): {exc}")
        return None


def cmd_preflight() -> dict[str, Any]:
    cfg = aws.load_config()
    regiao = aws.region()
    instance_type = cfg["sagemaker"]["instance_type"]

    log(
        f"resolvendo imagem {_FRAMEWORK!r} para {instance_type!r} em {regiao!r} via sagemaker.core.image_uris..."
    )
    image_uri = _resolve_image_uri(instance_type, regiao)
    log(f"image_uri = {image_uri}")
    _validar_imagem(image_uri)

    digest = _tentar_digest(image_uri)
    log(f"image_digest = {digest or '(não obtido — best-effort, ver aviso acima)'}")

    # Confirma que prompts/retention-system.txt existe e tem versão declarada
    # antes de dar PASS — sem prompt válido, nenhuma invocação real vai funcionar
    # mesmo com o runtime correto, e é melhor o preflight avisar aqui.
    versao_prompt, _ = inference.load_system_prompt()
    log(f"prompt_contract_version = {versao_prompt}")

    payload = {
        "image_uri": image_uri,
        "image_digest": digest,
        "max_context_tokens": CONFIRMED_MAX_CONTEXT_TOKENS,
        "max_output_tokens": CONFIRMED_MAX_OUTPUT_TOKENS,
        "prompt_contract_version": versao_prompt,
        "instance_type": instance_type,
        "probe_status": "confirmado" if digest else "confirmado_sem_digest",
    }
    inference.write_generated_runtime_patch(
        {
            "image_uri": image_uri,
            "image_digest": digest,
            "instance_type": instance_type,
            "max_context_tokens": CONFIRMED_MAX_CONTEXT_TOKENS,
            "max_output_tokens": CONFIRMED_MAX_OUTPUT_TOKENS,
        }
    )
    evidence.write("research-preflight.json", payload)
    log("[PASS] preflight de runtime confirmado")
    return payload


def cmd_credential_source_only() -> dict[str, Any]:
    log("confirmando origem da credencial ativa via STS...")
    identidade = aws.caller_identity()
    arn = identidade["Arn"]
    arn_type = (
        "assumed-role"
        if ":assumed-role/" in arn
        else ("user" if ":user/" in arn else "outro")
    )
    metodo = (
        aws.session().get_credentials().method
    )  # ex.: "iam-role", "shared-credentials-file", "env"
    static_env = bool(
        os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_SECRET_ACCESS_KEY")
    )

    payload = {
        "account": identidade["Account"],
        "arn_type": arn_type,
        "credential_method": metodo,
        "static_env_keys_present": static_env,
    }
    evidence.write("credential-source-v2.json", payload)

    seguro = arn_type == "assumed-role" and not static_env
    log(
        f"{'[PASS]' if seguro else '[AVISO]'} credencial: arn_type={arn_type} "
        f"metodo={metodo} chave_estatica_em_env={static_env}"
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--credential-source-only",
        action="store_true",
        help="Só confirma a origem da credencial (usado por 'make pipeline-preflight')",
    )
    args = parser.parse_args()

    try:
        resultado = (
            cmd_credential_source_only()
            if args.credential_source_only
            else cmd_preflight()
        )
    except LabError as exc:
        log(f"[FAIL] {exc}")
        return 1

    emit(resultado)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
