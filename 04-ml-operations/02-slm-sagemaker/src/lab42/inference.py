"""Único ponto de contato com o contrato de invocação do SLM (SageMaker Runtime).

Contrato provado por execução real (não documentação copiada): o probe do
agente A2 subiu o endpoint `huggingface-llamacpp` (llama.cpp CPU DLC) e
confirmou que `InvokeEndpoint` repassa, sem transformação, um corpo
OpenAI-style para o `llama-server` interno — ver a captura literal de
request/response em `contrato-inferencia.md` da coordenação. `invoke.py`,
`evaluate.py` e `benchmark.py` chamam só as funções deste módulo: nenhum
outro script monta o payload à mão, para o contrato nunca se espalhar por dez
lugares (regra dura do blueprint §7).
"""

from __future__ import annotations

import functools
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError

from . import aws

PROMPT_PATH = aws.LAB_ROOT / "prompts" / "retention-system.txt"

# Teto de projeto confirmado pelo probe real (não um chute): max_tokens=64 e
# temperature=0 são os valores com que o A2 provou o contrato na conta real,
# e são os mesmos gravados em model/releases/v1.yaml e v2.yaml.
DEFAULT_MAX_TOKENS = 64
DEFAULT_TEMPERATURE = 0.0

# Ordem fixa dos campos do contexto sintético — a mesma do exemplo do §8 da
# spec. Fixar a ordem (em vez de usar a ordem de inserção do dict do caller)
# mantém o texto do prompt estável entre chamadas, o que ajuda o prompt cache
# do llama.cpp server (ver observação 5 de contrato-inferencia.md).
_CONTEXT_FIELD_ORDER = (
    "probabilidade_churn",
    "faixa_tempo_cliente",
    "plano",
    "uso",
    "chamados_90d",
    "atraso_pagamento",
)

# Alias público de `_CONTEXT_FIELD_ORDER` — `scripts/invoke.py` usa esta
# lista para validar `--context-file` (whitelist de campos, nunca shell/eval
# livre: um arquivo com chave fora desta lista é rejeitado antes de qualquer
# chamada à rede).
ALLOWED_CONTEXT_FIELDS = _CONTEXT_FIELD_ORDER

_VERSION_RE = re.compile(r"prompt_contract_version:\s*(\S+)")


class InferenceProtocolError(aws.LabError):
    """Falha de transporte/protocolo: sem 200 do SageMaker ou corpo não é JSON.

    Cobre rede, endpoint fora de `InService`, `ModelError` do container e
    corpo ilegível — nunca é sobre o CONTEÚDO da resposta (ver
    `InferenceContentError` para essa outra categoria).
    """


class InferenceContentError(aws.LabError):
    """Protocolo teve sucesso, mas o corpo não respeita o contrato OpenAI-style
    esperado (sem `choices`, mensagem vazia, schema quebrado). Separar as duas
    categorias de erro é o que permite ao aluno diferenciar "o endpoint caiu"
    de "o endpoint respondeu algo fora do contrato".
    """


@dataclass(frozen=True)
class InferenceResult:
    """Resultado de uma invocação — o que `evaluate.py`/`benchmark.py` precisam."""

    text: str
    finish_reason: str | None
    latency_s: float
    request: dict[str, Any]
    response: dict[str, Any]
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    timings: dict[str, Any] | None


@functools.lru_cache(maxsize=1)
def load_system_prompt() -> tuple[str, str]:
    """Lê `prompts/retention-system.txt`: devolve `(versão, texto enviado ao modelo)`.

    As linhas de comentário (prefixo `#`) no topo do arquivo são metadado do
    lab (versão, explicação de por que o contrato existe) e NUNCA são
    enviadas ao modelo — só o texto a partir da primeira linha sem `#` vira o
    conteúdo da mensagem `system`. Isso deixa o arquivo autoexplicativo para
    quem abre no editor sem poluir o prompt real com comentário de humano.
    """
    if not PROMPT_PATH.exists():
        raise aws.LabError(
            f"{PROMPT_PATH} não existe — o contrato de prompt é obrigatório."
        )

    lines = PROMPT_PATH.read_text(encoding="utf-8").splitlines()
    version: str | None = None
    body_start = len(lines)
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            match = _VERSION_RE.search(stripped)
            if match:
                version = match.group(1)
            continue
        body_start = idx
        break

    body = "\n".join(lines[body_start:]).strip()
    if not version:
        raise aws.LabError(
            f"{PROMPT_PATH} não declara 'prompt_contract_version' no cabeçalho de "
            "comentário — precisa casar com model/releases/v1.yaml e v2.yaml."
        )
    if not body:
        raise aws.LabError(
            f"{PROMPT_PATH} não tem conteúdo de prompt após o cabeçalho."
        )
    return version, body


def render_context(contexto: dict[str, Any]) -> str:
    """Monta a frase de contexto no formato provado pelo probe real do A2.

    `contexto` chega sempre sintético e sem PII (é contrato duro do lab — ver
    `eval/cases.yaml`); esta função só formata, nunca valida ausência de PII
    — quem valida isso é `evaluation.py`, olhando a RESPOSTA do modelo.
    """
    partes = [
        f"{campo}={contexto[campo]}"
        for campo in _CONTEXT_FIELD_ORDER
        if campo in contexto
    ]
    extras = [
        f"{chave}={valor}"
        for chave, valor in contexto.items()
        if chave not in _CONTEXT_FIELD_ORDER
    ]
    corpo = ", ".join(partes + extras)
    return (
        f"Contexto do cliente: {corpo}. Em até 3 frases, explique o risco "
        "observado e sugira a próxima ação para o atendente."
    )


def build_request(
    contexto: dict[str, Any],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
) -> dict[str, Any]:
    """Corpo OpenAI-style exato do contrato provado (ver contrato-inferencia.md).

    Único lugar do lab que monta este dicionário — `invoke.py`, `evaluate.py`
    e `benchmark.py` chamam esta função em vez de duplicar o payload.
    """
    _, system_prompt = load_system_prompt()
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": render_context(contexto)},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


def resolve_endpoint_name(release: str) -> str:
    """Nome do endpoint, determinístico a partir de `config/lab.yaml["naming"]`.

    Nunca lido de `terraform output`: o nome é função pura de
    `account_id + github_owner` (a mesma fórmula que o Terraform usa para
    nomear o recurso — `config/lab.yaml["naming"]["suffix_algorithm"]`), então
    resolver por aqui não cria dependência cruzada com o state de
    `terraform/slm` de outro agente, e funciona mesmo antes do apply.
    """
    if release not in ("v1", "v2"):
        raise aws.LabError(f"release inválida: {release!r} — use 'v1' ou 'v2'.")
    template = aws.load_config()["naming"][f"endpoint_{release}"]
    return aws.resource_name(template)


def invoke(
    endpoint_name: str,
    contexto: dict[str, Any],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
) -> InferenceResult:
    """Uma chamada real ao endpoint.

    A latência medida é de ponta a ponta (o mesmo `time.perf_counter()` em
    torno de `invoke_endpoint` que o probe do A2 usou) — não o
    `timings.predicted_ms` que o llama.cpp devolve no corpo, que só cobre a
    geração de tokens e ignora fila/rede/overhead do próprio SageMaker.
    """
    request = build_request(contexto, max_tokens=max_tokens, temperature=temperature)
    body = json.dumps(request, ensure_ascii=False).encode("utf-8")

    t0 = time.perf_counter()
    try:
        raw = aws.client("sagemaker-runtime").invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="application/json",
            Accept="application/json",
            Body=body,
        )
    except ClientError as exc:
        detalhe = exc.response.get("Error", {}).get("Message", str(exc))
        raise InferenceProtocolError(
            f"InvokeEndpoint falhou em {endpoint_name!r}: {detalhe}. Confira "
            "'make status-v1' (ou status-v2) — o endpoint pode não estar InService."
        ) from exc
    latency_s = time.perf_counter() - t0

    payload = raw["Body"].read()
    try:
        response = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InferenceProtocolError(
            f"resposta de {endpoint_name!r} não é JSON válido: {payload[:200]!r}"
        ) from exc

    choices = response.get("choices")
    if not choices or not isinstance(choices, list):
        raise InferenceContentError(
            f"resposta de {endpoint_name!r} não tem 'choices' — contrato OpenAI-style "
            f"quebrado. Corpo recebido: {json.dumps(response, ensure_ascii=False)[:300]}"
        )
    message = choices[0].get("message") or {}
    text = message.get("content")
    if text is None:
        raise InferenceContentError(
            f"resposta de {endpoint_name!r} não tem 'choices[0].message.content'."
        )

    usage = response.get("usage") or {}
    return InferenceResult(
        text=text,
        finish_reason=choices[0].get("finish_reason"),
        latency_s=latency_s,
        request=request,
        response=response,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        timings=response.get("timings"),
    )


def invoke_release(
    release: str,
    contexto: dict[str, Any],
    *,
    endpoint_name: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
) -> InferenceResult:
    """Atalho: resolve o nome do endpoint pela release, a menos que `endpoint_name` seja dado."""
    nome = endpoint_name or resolve_endpoint_name(release)
    return invoke(nome, contexto, max_tokens=max_tokens, temperature=temperature)


# --------------------------------------------------------------------------- #
# `.generated/runtime.json` — arquivo compartilhado entre `resolve_runtime.py`
# (grava imagem/contexto no preflight) e `benchmark.py` (grava o envelope de
# latência depois do benchmark real). Nenhum dos dois pode sobrescrever o
# arquivo inteiro, só mesclar a própria parte — por isso o merge fica aqui,
# num único lugar, em vez de cada script reimplementar leitura+escrita.
# --------------------------------------------------------------------------- #

GENERATED_RUNTIME_PATH = aws.GENERATED_DIR / "runtime.json"


def read_generated_runtime() -> dict[str, Any]:
    """Lê `.generated/runtime.json`, ou `{}` se o preflight ainda não rodou nesta máquina."""
    if not GENERATED_RUNTIME_PATH.exists():
        return {}
    return json.loads(GENERATED_RUNTIME_PATH.read_text(encoding="utf-8"))


def write_generated_runtime_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Mescla `patch` no `.generated/runtime.json` existente — nunca sobrescreve o arquivo todo."""
    aws.ensure_dirs()
    data = read_generated_runtime()
    data.update(patch)
    GENERATED_RUNTIME_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return data
