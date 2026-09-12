"""Reação automática ao alarme de drift — abre incidente, não retreina.

Esta função é o "sistema reagindo sozinho" do laboratório. O que ela faz é
deliberadamente pequeno:

1. valida que o evento é MESMO a entrada em ALARM do alarme deste lab;
2. escreve um JSON de incidente no S3, com o que foi observado e o que se
   recomenda;
3. publica `ReactionTriggered=1` no CloudWatch, para o dashboard provar que a
   reação aconteceu.

O que ela **não** faz, e é isso que a aula precisa levar embora:

* não cria training job;
* não troca o endpoint;
* não promove modelo nenhum;
* não gera churn-v2.

Retreinar automaticamente porque o PSI subiu é institucionalizar erro. Drift diz
que a distribuição de entrada mudou — não diz que o modelo errou. Se o mundo mudou
e o modelo continua certo, o retraining automático joga fora um modelo bom; se o
rótulo verdadeiro ainda não chegou (e no churn ele demora dias), o retraining
treina contra uma verdade que ninguém conferiu. A reação segura é abrir incidente e
segurar promoção até alguém olhar o ground truth.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SCHEMA_VERSION = "1.0.0"

# A decisão que esta função tem autoridade para tomar. Investigar e segurar
# promoção — nunca agir sobre o modelo.
DECISION_STATUS = "INVESTIGATE"
DECISION_ACTION = "HOLD_PROMOTION_AND_VALIDATE_GROUND_TRUTH"

NOT_AUTOMATED = [
    "criar training job",
    "trocar o endpoint em produção",
    "promover modelo novo",
    "publicar churn-v2",
]


class EventoInvalido(Exception):
    """O evento recebido não é a transição para ALARM que esta função trata."""


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"variável de ambiente obrigatória ausente: {name}")
    return value


def validar_evento(event: Any) -> dict[str, Any]:
    """Aceita só o que deve ser aceito, e diz por que recusou o resto.

    A regra do EventBridge já filtra origem, tipo e estado, mas a validação é
    repetida aqui de propósito: uma Lambda que confia no filtro de quem a chama é
    uma Lambda que quebra quando alguém reaproveita a função em outra regra — ou
    quando um teste manual manda um payload à mão.
    """
    if not isinstance(event, dict):
        raise EventoInvalido(f"o evento não é um objeto JSON: {type(event).__name__}")

    if event.get("source") != "aws.cloudwatch":
        raise EventoInvalido(f"origem inesperada: {event.get('source')!r}")

    if event.get("detail-type") != "CloudWatch Alarm State Change":
        raise EventoInvalido(f"detail-type inesperado: {event.get('detail-type')!r}")

    detail = event.get("detail")
    if not isinstance(detail, dict):
        raise EventoInvalido("o evento não traz o objeto 'detail'")

    nome_alarme = detail.get("alarmName")
    esperado = os.environ.get("EXPECTED_ALARM", "")
    if esperado and nome_alarme != esperado:
        raise EventoInvalido(
            f"alarme {nome_alarme!r} não é o alarme deste lab ({esperado!r})"
        )

    novo_estado = (detail.get("state") or {}).get("value")
    if novo_estado != "ALARM":
        raise EventoInvalido(f"estado {novo_estado!r} não é ALARM; nada a fazer")

    return detail


def _numeros_do_alarme(detail: dict[str, Any]) -> dict[str, Any]:
    """Extrai limiar e datapoint observado do 'reasonData' do alarme.

    O CloudWatch manda esse campo como STRING contendo JSON. Ele é a única fonte,
    dentro do evento, do valor que de fato cruzou o limiar — sem ele o incidente
    diria "houve drift" sem dizer quanto.
    """
    estado = detail.get("state") or {}
    dados: dict[str, Any] = {}
    bruto = estado.get("reasonData")
    if isinstance(bruto, str):
        try:
            dados = json.loads(bruto)
        except json.JSONDecodeError:
            logger.warning("reasonData não é JSON válido; seguindo sem os números")

    datapoints = dados.get("recentDatapoints") or []
    return {
        "limiar": dados.get("threshold"),
        "estatistica": dados.get("statistic"),
        "periodo_s": dados.get("period"),
        "datapoints_recentes": datapoints,
        "valor_observado": datapoints[-1] if datapoints else None,
        "motivo": estado.get("reason"),
    }


def montar_incidente(detail: dict[str, Any], request_id: str) -> dict[str, Any]:
    """Monta o documento de incidente. Toda afirmação vem do evento ou do ambiente."""
    numeros = _numeros_do_alarme(detail)
    agora = datetime.now(timezone.utc)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": DECISION_STATUS,
        "recommended_action": DECISION_ACTION,
        "detectado_em": agora.isoformat(),
        "lambda_request_id": request_id,
        "alarme": {
            "nome": detail.get("alarmName"),
            "estado_anterior": (detail.get("previousState") or {}).get("value"),
            "estado_novo": (detail.get("state") or {}).get("value"),
            "motivo": numeros["motivo"],
        },
        "metrica": {
            "namespace": os.environ.get("METRICS_NAMESPACE"),
            "nome": "DataDriftPSIMax",
            "limiar": numeros["limiar"],
            "valor_observado": numeros["valor_observado"],
            "estatistica": numeros["estatistica"],
            "periodo_s": numeros["periodo_s"],
        },
        "endpoint": os.environ.get("ENDPOINT_NAME"),
        "model_lineage": os.environ.get("MODEL_LINEAGE"),
        "leitura": (
            "A distribuição de entrada saiu do mundo em que o modelo foi treinado. "
            "Isso é sinal, não sentença: drift de dados não prova queda de qualidade. "
            "A confirmação só vem quando o ground truth chegar."
        ),
        "nao_automatizado": NOT_AUTOMATED,
        "proximo_passo_humano": (
            "Rodar a avaliação com ground truth atrasado, comparar F1/ROC-AUC com a "
            "janela baseline e só então decidir sobre retraining."
        ),
    }


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    """Ponto de entrada. Nunca levanta exceção por evento inválido.

    Um evento fora do contrato é ignorado com log explicativo, não com erro: um
    `raise` aqui faria o EventBridge reentregar o mesmo payload inválido várias
    vezes e encheria o log de ruído que não ajuda ninguém a diagnosticar.
    """
    request_id = getattr(context, "aws_request_id", "local")

    try:
        detail = validar_evento(event)
    except EventoInvalido as exc:
        logger.warning("evento ignorado: %s", exc)
        return {"status": "IGNORADO", "motivo": str(exc)}

    incidente = montar_incidente(detail, request_id)

    bucket = _env("LAB_BUCKET")
    prefixo = os.environ.get("INCIDENTS_PREFIX", "incidents")
    carimbo = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    chave = f"{prefixo}/{carimbo}-{request_id}.json"

    corpo = json.dumps(incidente, indent=2, ensure_ascii=False).encode("utf-8")
    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=chave,
        Body=corpo,
        ContentType="application/json",
    )
    logger.info("incidente escrito em s3://%s/%s", bucket, chave)

    # A métrica é o que faz a reação aparecer no dashboard. Sem ela, a única prova
    # de que a Lambda rodou estaria no log — e log não é painel.
    boto3.client("cloudwatch").put_metric_data(
        Namespace=_env("METRICS_NAMESPACE"),
        MetricData=[
            {
                "MetricName": "ReactionTriggered",
                "Dimensions": [
                    {"Name": "EndpointName", "Value": os.environ.get("ENDPOINT_NAME", "desconhecido")}
                ],
                "Value": 1.0,
                "Unit": "Count",
            }
        ],
    )
    logger.info(
        "ReactionTriggered publicado; decisão=%s ação=%s",
        incidente["status"],
        incidente["recommended_action"],
    )

    return {
        "status": incidente["status"],
        "recommended_action": incidente["recommended_action"],
        "incidente_s3": f"s3://{bucket}/{chave}",
    }
