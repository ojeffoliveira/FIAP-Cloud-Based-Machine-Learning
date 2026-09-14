"""Reação automática a drift — abre incidente, não retreina, não decide nada.

O que esta função faz é deliberadamente pequeno:

1. valida que o evento é MESMO a entrada em ALARM de um dos dois alarmes de
   drift deste trabalho final (data_drift ou prediction_drift);
2. escreve um JSON de incidente no S3, com o que foi observado;
3. publica `ReactionTriggered=1` no CloudWatch, para o dashboard provar que a
   reação aconteceu.

O que ela **não** faz, e é isso que o trabalho final precisa levar embora:

* não cria training job;
* não troca o endpoint, nem escolhe qual dos três (Real-Time/Serverless/
  Async) deveria estar em produção;
* não promove modelo nenhum, não decide rollback, não decide go-live.

Drift diz que a distribuição de entrada (ou de predição) mudou — não diz que
o modelo errou. Reagir sozinho e retreinar/trocar modelo com base só em PSI
institucionaliza erro: se o mundo mudou e o modelo continua certo, o
retraining automático joga fora um modelo bom; se o ground truth ainda não
chegou (e no cenário Bora Fibra ele chega atrasado, de propósito), o
retraining treina contra uma verdade que ninguém conferiu. A reação segura é
abrir incidente e segurar qualquer promoção até um humano olhar a evidência —
essa decisão final é do aluno, em student/DECISION.md, nunca desta função.
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

# A decisão que esta função tem autoridade para tomar: investigar e segurar
# qualquer promoção. Nunca agir sobre modelo, endpoint ou pattern de serving.
DECISION_STATUS = "INVESTIGATE"
DECISION_ACTION = "HOLD_PROMOTION_AND_VALIDATE_GROUND_TRUTH"

NOT_AUTOMATED = [
    "criar training job",
    "trocar o endpoint em produção",
    "escolher qual pattern de serving atender",
    "promover modelo novo",
    "decidir rollback ou go-live",
]

# Mapa nome-do-alarme -> métrica de origem, resolvido a partir do ambiente
# (os nomes vêm de local.data_drift_alarm_name/local.prediction_drift_alarm_name
# em terraform/lambda.tf, nunca de um valor fixo no código).
_METRIC_BY_ALARM_ENV = {
    "DATA_DRIFT_ALARM_NAME": "DataDriftPSIMax",
    "PREDICTION_DRIFT_ALARM_NAME": "PredictionDriftPSI",
}


class EventoInvalido(Exception):
    """O evento recebido não é a transição para ALARM de um alarme conhecido."""


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"variável de ambiente obrigatória ausente: {name}")
    return value


def _metrica_do_alarme(nome_alarme: str) -> str:
    """Resolve qual métrica gerou o alarme, comparando com os dois nomes esperados."""
    for env_var, metrica in _METRIC_BY_ALARM_ENV.items():
        esperado = os.environ.get(env_var, "")
        if esperado and nome_alarme == esperado:
            return metrica
    raise EventoInvalido(
        f"alarme {nome_alarme!r} não é nenhum dos dois alarmes deste trabalho final"
    )


def validar_evento(event: Any) -> dict[str, Any]:
    """Aceita só o que deve ser aceito, e diz por que recusou o resto.

    A regra do EventBridge (eventbridge.tf) já filtra origem, tipo, ARN do
    alarme e estado, mas a validação é repetida aqui de propósito: uma Lambda
    que confia no filtro de quem a chama quebra quando alguém reaproveita a
    função em outra regra, ou quando um teste manual manda um payload à mão.
    Qualquer desvio do contrato é rejeitado com log claro, sem processar nada.
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
    if not isinstance(nome_alarme, str) or not nome_alarme:
        raise EventoInvalido(f"alarmName ausente ou inválido: {nome_alarme!r}")

    novo_estado = (detail.get("state") or {}).get("value")
    if novo_estado != "ALARM":
        raise EventoInvalido(f"estado {novo_estado!r} não é ALARM; nada a fazer")

    # Levanta EventoInvalido se o nome não bater com nenhum dos dois alarmes
    # conhecidos — feito aqui, antes de montar qualquer incidente.
    _metrica_do_alarme(nome_alarme)

    return detail


def _numeros_do_alarme(detail: dict[str, Any]) -> dict[str, Any]:
    """Extrai limiar e datapoint observado do 'reasonData' do alarme.

    O CloudWatch manda esse campo como STRING contendo JSON. É a única fonte,
    dentro do evento, do valor que de fato cruzou o limiar.
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
    nome_alarme = detail.get("alarmName", "")
    metrica = _metrica_do_alarme(nome_alarme)
    numeros = _numeros_do_alarme(detail)
    agora = datetime.now(timezone.utc)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": DECISION_STATUS,
        "recommended_action": DECISION_ACTION,
        "detectado_em": agora.isoformat(),
        "lambda_request_id": request_id,
        "alarme": {
            "nome": nome_alarme,
            "estado_anterior": (detail.get("previousState") or {}).get("value"),
            "estado_novo": (detail.get("state") or {}).get("value"),
            "motivo": numeros["motivo"],
        },
        "metrica": {
            "namespace": os.environ.get("METRICS_NAMESPACE"),
            "nome": metrica,
            "limiar": numeros["limiar"] if numeros["limiar"] is not None else os.environ.get("PSI_THRESHOLD"),
            "valor_observado": numeros["valor_observado"],
            "estatistica": numeros["estatistica"],
            "periodo_s": numeros["periodo_s"],
        },
        "model_lineage": os.environ.get("MODEL_LINEAGE"),
        "leitura": (
            "A distribuição de entrada ou de predição saiu do mundo em que o "
            "modelo foi treinado. Isso é sinal, não sentença: drift não prova "
            "queda de qualidade. A confirmação só vem quando o ground truth "
            "atrasado chegar."
        ),
        "nao_automatizado": NOT_AUTOMATED,
        "proximo_passo_humano": (
            "Rodar a avaliação com o ground truth atrasado, comparar F1/ROC-AUC "
            "com a janela baseline e só então registrar a decisão em "
            "student/DECISION.md."
        ),
    }


def lambda_handler(event: Any, context: Any) -> dict[str, Any]:
    """Ponto de entrada. Nunca levanta exceção crua por evento inválido.

    Um evento fora do contrato é ignorado com log explicativo, não com erro:
    um `raise` aqui faria o EventBridge reentregar o mesmo payload inválido
    várias vezes e encheria o log de ruído que não ajuda ninguém a
    diagnosticar. Evento malformado é rejeitado e não processado — nenhum
    incidente é escrito, nenhuma métrica é publicada.
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

    # A métrica é o que faz a reação aparecer no dashboard. Sem ela, a única
    # prova de que a Lambda rodou estaria no log — e log não é painel.
    boto3.client("cloudwatch").put_metric_data(
        Namespace=_env("METRICS_NAMESPACE"),
        MetricData=[
            {
                "MetricName": "ReactionTriggered",
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
