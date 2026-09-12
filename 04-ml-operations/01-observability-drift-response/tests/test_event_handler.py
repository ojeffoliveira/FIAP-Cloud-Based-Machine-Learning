"""Testes do handler da Lambda de reação.

Dois grupos, com propósitos diferentes:

* **contrato de decisão** — a função tem de registrar INVESTIGATE e segurar
  promoção, e NUNCA disparar retraining. Esse é o conteúdo pedagógico do
  laboratório, então está travado por teste.
* **robustez de evento** — a regra do EventBridge filtra, mas um payload à mão, um
  reaproveitamento da função em outra regra ou um teste do console mandam qualquer
  coisa. Evento inválido é ignorado com motivo, nunca levanta exceção (um `raise`
  faria o EventBridge reentregar o mesmo payload inválido várias vezes).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

LAB_ROOT = Path(__file__).resolve().parents[1]

# A pasta se chama `lambda`, que é palavra reservada em Python: não há como
# escrever `import lambda.drift_response`. O carregamento por caminho é a saída.
_spec = importlib.util.spec_from_file_location(
    "drift_response", LAB_ROOT / "lambda" / "drift_response.py"
)
handler_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler_module)


ALARM_NAME = "prb-cloud-ml-lab3-drift-abcd1234"
ENDPOINT = "prb-cloud-ml-lab3-ep-abcd1234"


class FakeS3:
    def __init__(self) -> None:
        self.objects: list[dict] = []

    def put_object(self, **kwargs):
        self.objects.append(kwargs)
        return {}


class FakeCloudWatch:
    def __init__(self) -> None:
        self.metrics: list[dict] = []

    def put_metric_data(self, **kwargs):
        self.metrics.append(kwargs)
        return {}


class FakeContext:
    aws_request_id = "req-0001"


@pytest.fixture
def ambiente(monkeypatch):
    monkeypatch.setenv("LAB_BUCKET", "prb-cloud-ml-lab3-000000000000")
    monkeypatch.setenv("INCIDENTS_PREFIX", "incidents")
    monkeypatch.setenv("METRICS_NAMESPACE", "FIAP/ML/Operations")
    monkeypatch.setenv("ENDPOINT_NAME", ENDPOINT)
    monkeypatch.setenv("EXPECTED_ALARM", ALARM_NAME)
    monkeypatch.setenv("MODEL_LINEAGE", "churn-v1")

    s3, cloudwatch = FakeS3(), FakeCloudWatch()
    monkeypatch.setattr(
        handler_module.boto3,
        "client",
        lambda service, *a, **k: {"s3": s3, "cloudwatch": cloudwatch}[service],
    )
    return s3, cloudwatch


def evento_valido(**overrides):
    detail = {
        "alarmName": ALARM_NAME,
        "previousState": {"value": "OK"},
        "state": {
            "value": "ALARM",
            "reason": "Threshold Crossed: 1 datapoint [2.8671] was greater than or equal to the threshold (0.2).",
            "reasonData": json.dumps(
                {
                    "threshold": 0.2,
                    "statistic": "Maximum",
                    "period": 60,
                    "recentDatapoints": [2.8671],
                }
            ),
        },
    }
    detail.update(overrides.pop("detail", {}))
    evento = {
        "source": "aws.cloudwatch",
        "detail-type": "CloudWatch Alarm State Change",
        "detail": detail,
    }
    evento.update(overrides)
    return evento


# --------------------------------------------------------------------------- #
# contrato de decisão
# --------------------------------------------------------------------------- #


def test_evento_valido_escreve_incidente_e_publica_metrica(ambiente):
    s3, cloudwatch = ambiente

    resposta = handler_module.lambda_handler(evento_valido(), FakeContext())

    assert resposta["status"] == "INVESTIGATE"
    assert resposta["recommended_action"] == "HOLD_PROMOTION_AND_VALIDATE_GROUND_TRUTH"

    assert len(s3.objects) == 1
    escrito = s3.objects[0]
    assert escrito["Key"].startswith("incidents/")
    assert escrito["Key"].endswith(".json")
    assert escrito["ContentType"] == "application/json"

    incidente = json.loads(escrito["Body"].decode("utf-8"))
    assert incidente["alarme"]["nome"] == ALARM_NAME
    assert incidente["alarme"]["estado_anterior"] == "OK"
    assert incidente["metrica"]["limiar"] == 0.2
    assert incidente["metrica"]["valor_observado"] == 2.8671
    assert incidente["endpoint"] == ENDPOINT
    assert incidente["model_lineage"] == "churn-v1"

    assert len(cloudwatch.metrics) == 1
    datum = cloudwatch.metrics[0]["MetricData"][0]
    assert datum["MetricName"] == "ReactionTriggered"
    assert datum["Value"] == 1.0
    assert datum["Dimensions"] == [{"Name": "EndpointName", "Value": ENDPOINT}]


def test_a_reacao_declara_o_que_nao_automatiza(ambiente):
    s3, _ = ambiente
    handler_module.lambda_handler(evento_valido(), FakeContext())
    incidente = json.loads(s3.objects[0]["Body"].decode("utf-8"))

    nao_automatizado = " ".join(incidente["nao_automatizado"]).lower()
    assert "training job" in nao_automatizado
    assert "promover modelo" in nao_automatizado
    assert "endpoint" in nao_automatizado
    assert "churn-v2" in nao_automatizado


def test_o_incidente_diz_que_drift_nao_prova_queda_de_qualidade(ambiente):
    s3, _ = ambiente
    handler_module.lambda_handler(evento_valido(), FakeContext())
    incidente = json.loads(s3.objects[0]["Body"].decode("utf-8"))

    assert "sinal, não sentença" in incidente["leitura"]
    assert "ground truth" in incidente["proximo_passo_humano"]


def test_reason_data_invalido_nao_derruba_a_funcao(ambiente):
    """O incidente ainda deve ser aberto, mesmo sem os números do alarme."""
    s3, cloudwatch = ambiente
    evento = evento_valido(detail={"state": {"value": "ALARM", "reasonData": "não é json"}})

    resposta = handler_module.lambda_handler(evento, FakeContext())

    assert resposta["status"] == "INVESTIGATE"
    incidente = json.loads(s3.objects[0]["Body"].decode("utf-8"))
    assert incidente["metrica"]["limiar"] is None
    assert incidente["metrica"]["valor_observado"] is None
    assert len(cloudwatch.metrics) == 1


# --------------------------------------------------------------------------- #
# robustez de evento
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "evento,fragmento",
    [
        ("string solta", "não é um objeto JSON"),
        ({"source": "aws.s3", "detail-type": "CloudWatch Alarm State Change", "detail": {}}, "origem inesperada"),
        ({"source": "aws.cloudwatch", "detail-type": "Outra Coisa", "detail": {}}, "detail-type inesperado"),
        ({"source": "aws.cloudwatch", "detail-type": "CloudWatch Alarm State Change"}, "não traz o objeto 'detail'"),
    ],
)
def test_evento_malformado_e_ignorado_sem_excecao(ambiente, evento, fragmento):
    s3, cloudwatch = ambiente

    resposta = handler_module.lambda_handler(evento, FakeContext())

    assert resposta["status"] == "IGNORADO"
    assert fragmento in resposta["motivo"]
    assert s3.objects == []
    assert cloudwatch.metrics == []


def test_alarme_de_outro_lab_e_ignorado(ambiente):
    s3, _ = ambiente
    evento = evento_valido(detail={"alarmName": "alarme-de-outra-turma"})

    resposta = handler_module.lambda_handler(evento, FakeContext())

    assert resposta["status"] == "IGNORADO"
    assert "não é o alarme deste lab" in resposta["motivo"]
    assert s3.objects == []


def test_volta_para_ok_nao_abre_incidente(ambiente):
    """O EventBridge filtra por ALARM, mas a função não pode depender só disso."""
    s3, _ = ambiente
    evento = evento_valido(detail={"state": {"value": "OK", "reason": "voltou ao normal"}})

    resposta = handler_module.lambda_handler(evento, FakeContext())

    assert resposta["status"] == "IGNORADO"
    assert "não é ALARM" in resposta["motivo"]
    assert s3.objects == []


def test_variavel_de_ambiente_obrigatoria_ausente_falha_alto(ambiente, monkeypatch):
    """Configuração faltando é erro de infraestrutura, não evento inválido: deve estourar."""
    monkeypatch.delenv("LAB_BUCKET")

    with pytest.raises(RuntimeError, match="LAB_BUCKET"):
        handler_module.lambda_handler(evento_valido(), FakeContext())
