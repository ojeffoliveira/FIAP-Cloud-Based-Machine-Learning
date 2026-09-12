"""Publicação de métricas e leitura de alarme — a camada de observabilidade do lab.

O laboratório não usa SageMaker Model Monitor de propósito. O objetivo é que o
aluno veja o mecanismo inteiro: alguém calcula um número, alguém publica esse
número, um alarme compara o número com um limiar, e um evento nasce dessa
comparação. Com Model Monitor essa cadeia fica dentro de um serviço gerenciado e o
aluno aprende a ligar um recurso, não a operar um sistema.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config
from .aws import LabError, client, log

# Métrica que o alarme observa. Precisa ser publicada com um conjunto de dimensões
# IDÊNTICO ao declarado no alarme, senão o alarme fica em INSUFFICIENT_DATA para
# sempre.
ALARM_METRIC = "DataDriftPSIMax"


def _datum(
    name: str, value: float, dimensions: dict[str, str], unit: str = "None"
) -> dict[str, Any]:
    return {
        "MetricName": name,
        "Dimensions": [{"Name": k, "Value": v} for k, v in dimensions.items()],
        "Value": float(value),
        "Unit": unit,
    }


def publish(namespace: str, data: list[dict[str, Any]]) -> None:
    """Envia os datapoints e NARRA o que enviou.

    A narração é conteúdo de aula: o aluno precisa ver que "publicar métrica" é uma
    chamada de API com nome, valor e dimensões explícitos — não mágica de um agente
    instalado na instância.

    O PutMetricData aceita no máximo 1000 datapoints por chamada; o lab publica
    algumas dezenas, mas o fatiamento fica aqui para o número não ser uma bomba
    silenciosa se alguém aumentar o número de features.
    """
    if not data:
        return

    for datum in data:
        dims = ", ".join(f"{d['Name']}={d['Value']}" for d in datum["Dimensions"])
        log(f"[metricas] {datum['MetricName']}={datum['Value']:.6f}  [{dims}]")

    cloudwatch = client("cloudwatch")
    for start in range(0, len(data), 1000):
        cloudwatch.put_metric_data(
            Namespace=namespace, MetricData=data[start : start + 1000]
        )
    log(f"[metricas] {len(data)} datapoints publicados em {namespace}")


def window_metrics(
    endpoint_name: str,
    window: str,
    psi_max: float,
    feature_psi: dict[str, float],
    prediction_psi: float,
    predicted_churn_rate: float,
    mean_churn_probability: float,
) -> list[dict[str, Any]]:
    """Monta os datapoints de uma janela observada.

    Sobre `DataDriftPSIMax` aparecer DUAS vezes, com e sem a dimensão `Window`:
    no CloudWatch, mudar o conjunto de dimensões cria uma métrica DIFERENTE, não um
    filtro da mesma série. O alarme precisa de uma série estável, sem `Window`
    (senão ele observaria só a janela baseline e nunca veria o drift); o dashboard
    precisa das duas janelas separadas para a comparação ser visual. Publicar as
    duas versões é mais honesto que escolher uma e explicar depois por que o
    gráfico ou o alarme está vazio.
    """
    somente_endpoint = {"EndpointName": endpoint_name}
    com_janela = {"EndpointName": endpoint_name, "Window": window}

    data = [
        _datum(ALARM_METRIC, psi_max, somente_endpoint),
        _datum(ALARM_METRIC, psi_max, com_janela),
        _datum("PredictionDriftPSI", prediction_psi, somente_endpoint),
        _datum("PredictionDriftPSI", prediction_psi, com_janela),
        _datum("PredictedChurnRate", predicted_churn_rate, com_janela),
        _datum("MeanChurnProbability", mean_churn_probability, com_janela),
    ]
    # PSI por feature só com EndpointName: o eixo do tempo já separa baseline de
    # drift no gráfico, e sete features x duas janelas seriam catorze séries num
    # widget — exatamente o tipo de painel que ninguém lê.
    data += [
        _datum(f"DataDriftPSI_{feature}", value, somente_endpoint)
        for feature, value in feature_psi.items()
    ]
    return data


def quality_metrics(
    endpoint_name: str, window: str, f1: float, roc_auc: float
) -> list[dict[str, Any]]:
    """Datapoints de qualidade — só existem depois de o ground truth chegar."""
    dims = {"EndpointName": endpoint_name, "Window": window}
    return [
        _datum("ModelQualityF1", f1, dims),
        _datum("ModelQualityROCAUC", roc_auc, dims),
    ]


# --------------------------------------------------------------------------- #
# Alarme
# --------------------------------------------------------------------------- #


def alarm_state(alarm_name: str) -> dict[str, Any]:
    """Estado atual do alarme, com a razão que o CloudWatch registrou."""
    response = client("cloudwatch").describe_alarms(AlarmNames=[alarm_name])
    alarms = response.get("MetricAlarms", [])
    if not alarms:
        raise LabError(
            f"alarme {alarm_name!r} não encontrado. Rode `make apply` antes deste comando."
        )
    alarm = alarms[0]
    return {
        "nome": alarm["AlarmName"],
        "estado": alarm["StateValue"],
        "motivo": alarm.get("StateReason", ""),
        "atualizado_em": alarm.get("StateUpdatedTimestamp"),
        "limiar": alarm.get("Threshold"),
        "metrica": alarm.get("MetricName"),
        "estatistica": alarm.get("Statistic"),
        "periodo_s": alarm.get("Period"),
        "tratamento_de_ausencia": alarm.get("TreatMissingData"),
    }


def wait_for_alarm_state(
    alarm_name: str, target: str, timeout_s: int, poll_seconds: int = 15
) -> dict[str, Any]:
    """Espera o alarme chegar a um estado, com teto de tempo.

    Por que esperar em vez de conferir uma vez: o CloudWatch avalia o alarme em
    ciclo próprio, então existe latência real entre o PutMetricData e a transição de
    estado. Ela varia — o lab não publica uma faixa estreita de espera, publica o
    teto e imprime o progresso.
    """
    deadline = time.monotonic() + timeout_s
    ultimo = alarm_state(alarm_name)

    while ultimo["estado"] != target:
        if time.monotonic() >= deadline:
            raise LabError(
                f"o alarme {alarm_name} continua em {ultimo['estado']} depois de "
                f"{timeout_s}s de espera pelo estado {target}. Motivo registrado pelo "
                f"CloudWatch: {ultimo['motivo']!r}. Confira se o `make drift` publicou "
                "DataDriftPSIMax acima do limiar."
            )
        log(f"[alarme] estado {ultimo['estado']}; esperando {target} ({poll_seconds}s)")
        time.sleep(poll_seconds)
        ultimo = alarm_state(alarm_name)

    log(f"[alarme] estado {ultimo['estado']}")
    return ultimo


def alarm_history(alarm_name: str, limit: int = 10) -> list[dict[str, Any]]:
    """Transições recentes do alarme — a trilha de como ele chegou onde está."""
    response = client("cloudwatch").describe_alarm_history(
        AlarmName=alarm_name,
        HistoryItemType="StateUpdate",
        MaxRecords=limit,
        StartDate=datetime.now(timezone.utc) - timedelta(hours=6),
        EndDate=datetime.now(timezone.utc),
    )
    return [
        {"data": item["Timestamp"], "resumo": item["HistorySummary"]}
        for item in response.get("AlarmHistoryItems", [])
    ]


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #


def dashboard_summary(dashboard_name: str) -> dict[str, Any]:
    """Confere que o dashboard existe e conta os widgets declarados nele."""
    import json

    try:
        response = client("cloudwatch").get_dashboard(DashboardName=dashboard_name)
    except client("cloudwatch").exceptions.ResourceNotFound as exc:
        raise LabError(
            f"dashboard {dashboard_name!r} não existe. Rode `make apply`."
        ) from exc

    body = json.loads(response["DashboardBody"])
    widgets = body.get("widgets", [])
    return {
        "nome": dashboard_name,
        "arn": response.get("DashboardArn"),
        "widgets": len(widgets),
        "tipos": sorted({widget.get("type", "?") for widget in widgets}),
    }


def metric_datapoint_count(namespace: str, metric_name: str, endpoint_name: str) -> int:
    """Quantos datapoints existem para uma métrica na última hora.

    Serve ao `make evidence`: afirmar "o dashboard está populado" sem contar
    datapoint é afirmar sem conferir.
    """
    response = client("cloudwatch").get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=[{"Name": "EndpointName", "Value": endpoint_name}],
        StartTime=datetime.now(timezone.utc) - timedelta(hours=1),
        EndTime=datetime.now(timezone.utc),
        Period=60,
        Statistics=["Maximum"],
    )
    return len(response.get("Datapoints", []))


def namespace() -> str:
    return str(config.load_config()["monitoring"]["namespace"])
