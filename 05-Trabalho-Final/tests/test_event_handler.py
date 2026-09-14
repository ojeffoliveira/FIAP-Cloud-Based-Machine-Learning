"""Testes do handler da Lambda de reação a drift (`lambda/drift_response.py`).

Três grupos:

* **contrato de decisão** — a função abre incidente com status INVESTIGATE e
  segura qualquer promoção; NUNCA toca em SageMaker, troca modelo/endpoint ou
  decide rollback/go-live. Esse é o conteúdo pedagógico do trabalho final —
  travado por teste, não só por comentário.
* **robustez de evento** — a regra do EventBridge já filtra origem/tipo/ARN/
  estado, mas um payload à mão, reaproveitamento em outra regra ou teste de
  console mandam qualquer coisa. Evento fora do contrato é ignorado com
  motivo, nunca levanta excecão crua (um `raise` faria o EventBridge
  reentregar o mesmo payload inválido repetidas vezes).
* **configuração ausente falha alto** — env var obrigatória faltando é erro
  de infraestrutura, não evento inválido: isso sim deve estourar.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

LAB_ROOT = Path(__file__).resolve().parents[1]

# A pasta se chama `lambda`, palavra reservada em Python — não há como
# escrever `import lambda.drift_response`. Carregamento por caminho é a saída.
_spec = importlib.util.spec_from_file_location(
    "drift_response_final", LAB_ROOT / "lambda" / "drift_response.py"
)
handler_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler_module)


DATA_DRIFT_ALARM = "fiap-final-aluno-data-drift-abcd1234"
PREDICTION_DRIFT_ALARM = "fiap-final-aluno-prediction-drift-abcd1234"
ALARME_DE_OUTRO_LAB = "prb-cloud-ml-lab3-drift-abcd1234"


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


class ForbiddenClient:
    """Qualquer client que não seja s3/cloudwatch é proibido para esta
    função — nomeadamente sagemaker. Em vez de estourar na hora, registra a
    chamada em `sink`, para o teste poder listar exatamente o que aconteceu
    de indevido em vez de só ver uma pilha de excecão."""

    def __init__(self, service: str, sink: list[tuple[str, str]]) -> None:
        self._service = service
        self._sink = sink

    def __getattr__(self, method_name: str):
        def _record(**kwargs):
            self._sink.append((self._service, method_name))
            return {}

        return _record


class FakeContext:
    aws_request_id = "req-0001"


@pytest.fixture
def ambiente(monkeypatch):
    monkeypatch.setenv("LAB_BUCKET", "fiap-final-aluno-000000000000")
    monkeypatch.setenv("INCIDENTS_PREFIX", "incidents")
    monkeypatch.setenv("METRICS_NAMESPACE", "FIAP/BoraFibra/Final")
    monkeypatch.setenv("MODEL_LINEAGE", "churn-v1")
    monkeypatch.setenv("DATA_DRIFT_ALARM_NAME", DATA_DRIFT_ALARM)
    monkeypatch.setenv("PREDICTION_DRIFT_ALARM_NAME", PREDICTION_DRIFT_ALARM)

    s3, cloudwatch = FakeS3(), FakeCloudWatch()
    chamadas_proibidas: list[tuple[str, str]] = []

    def fake_client(service, *a, **k):
        if service == "s3":
            return s3
        if service == "cloudwatch":
            return cloudwatch
        return ForbiddenClient(service, chamadas_proibidas)

    monkeypatch.setattr(handler_module.boto3, "client", fake_client)
    return s3, cloudwatch, chamadas_proibidas


def evento_valido(alarm_name: str = DATA_DRIFT_ALARM, **overrides):
    detail = {
        "alarmName": alarm_name,
        "previousState": {"value": "OK"},
        "state": {
            "value": "ALARM",
            "reason": "Threshold Crossed: 1 datapoint [2.05] was greater than or equal to the threshold (0.2).",
            "reasonData": json.dumps(
                {
                    "threshold": 0.2,
                    "statistic": "Maximum",
                    "period": 300,
                    "recentDatapoints": [2.05],
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


def test_evento_valido_escreve_incidente_e_publica_reaction_triggered(ambiente):
    s3, cloudwatch, _ = ambiente

    resposta = handler_module.lambda_handler(evento_valido(), FakeContext())

    assert resposta["status"] == "INVESTIGATE"
    assert resposta["recommended_action"] == "HOLD_PROMOTION_AND_VALIDATE_GROUND_TRUTH"

    assert len(s3.objects) == 1
    escrito = s3.objects[0]
    assert escrito["Key"].startswith("incidents/")
    assert escrito["Key"].endswith(".json")
    assert escrito["ContentType"] == "application/json"

    incidente = json.loads(escrito["Body"].decode("utf-8"))
    assert incidente["alarme"]["nome"] == DATA_DRIFT_ALARM
    assert incidente["alarme"]["estado_anterior"] == "OK"
    assert incidente["metrica"]["nome"] == "DataDriftPSIMax"
    assert incidente["metrica"]["limiar"] == 0.2
    assert incidente["metrica"]["valor_observado"] == 2.05
    assert incidente["model_lineage"] == "churn-v1"

    assert len(cloudwatch.metrics) == 1
    datum = cloudwatch.metrics[0]["MetricData"][0]
    assert datum["MetricName"] == "ReactionTriggered"
    assert datum["Value"] == 1.0


def test_evento_do_alarme_de_predicao_resolve_a_outra_metrica(ambiente):
    """Os dois alarmes existem (`DataDriftPSIMax` e `PredictionDriftPSI`) —
    a Lambda precisa distinguir qual dos dois disparou."""
    s3, _, _ = ambiente
    handler_module.lambda_handler(
        evento_valido(alarm_name=PREDICTION_DRIFT_ALARM), FakeContext()
    )
    incidente = json.loads(s3.objects[0]["Body"].decode("utf-8"))
    assert incidente["metrica"]["nome"] == "PredictionDriftPSI"


def test_reason_data_invalido_nao_derruba_a_funcao(ambiente):
    """O incidente ainda deve ser aberto, mesmo sem os números do alarme."""
    s3, cloudwatch, _ = ambiente
    evento = evento_valido(
        detail={"state": {"value": "ALARM", "reasonData": "não é json"}}
    )

    resposta = handler_module.lambda_handler(evento, FakeContext())

    assert resposta["status"] == "INVESTIGATE"
    incidente = json.loads(s3.objects[0]["Body"].decode("utf-8"))
    assert incidente["metrica"]["valor_observado"] is None
    assert len(cloudwatch.metrics) == 1


# --------------------------------------------------------------------------- #
# os três testes que protegem o propósito do trabalho
# --------------------------------------------------------------------------- #


def test_a_reacao_nao_chama_nenhuma_api_de_sagemaker(ambiente):
    """Inspeciona as chamadas feitas no mock: só s3.put_object e
    cloudwatch.put_metric_data são aceitáveis. `ForbiddenClient` registra
    qualquer outro client (sagemaker incluso) em vez de deixar passar."""
    s3, cloudwatch, chamadas_proibidas = ambiente

    handler_module.lambda_handler(evento_valido(), FakeContext())

    assert chamadas_proibidas == []
    assert len(s3.objects) == 1
    assert len(cloudwatch.metrics) == 1


def test_a_reacao_nao_troca_nem_promove_modelo(ambiente):
    s3, _, _ = ambiente
    handler_module.lambda_handler(evento_valido(), FakeContext())
    incidente = json.loads(s3.objects[0]["Body"].decode("utf-8"))

    nao_automatizado = " ".join(incidente["nao_automatizado"]).lower()
    assert "promover modelo" in nao_automatizado
    assert "trocar o endpoint" in nao_automatizado
    assert "escolher qual pattern de serving" in nao_automatizado


def test_a_reacao_nao_decide_go_live_ou_rollback(ambiente):
    s3, _, _ = ambiente
    resposta = handler_module.lambda_handler(evento_valido(), FakeContext())
    incidente = json.loads(s3.objects[0]["Body"].decode("utf-8"))

    assert (
        "decidir rollback ou go-live" in " ".join(incidente["nao_automatizado"]).lower()
    )
    # a decisão que a função TEM autoridade de tomar é só esta:
    assert resposta["status"] == "INVESTIGATE"
    assert resposta["recommended_action"] == "HOLD_PROMOTION_AND_VALIDATE_GROUND_TRUTH"


# --------------------------------------------------------------------------- #
# robustez de evento
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "evento,fragmento",
    [
        ("string solta", "não é um objeto JSON"),
        (
            {
                "source": "aws.s3",
                "detail-type": "CloudWatch Alarm State Change",
                "detail": {},
            },
            "origem inesperada",
        ),
        (
            {"source": "aws.cloudwatch", "detail-type": "Outra Coisa", "detail": {}},
            "detail-type inesperado",
        ),
        (
            {
                "source": "aws.cloudwatch",
                "detail-type": "CloudWatch Alarm State Change",
            },
            "não traz o objeto 'detail'",
        ),
        (
            {
                "source": "aws.cloudwatch",
                "detail-type": "CloudWatch Alarm State Change",
                "detail": [1, 2, 3],
            },
            "não traz o objeto 'detail'",
        ),
        (
            {
                "source": "aws.cloudwatch",
                "detail-type": "CloudWatch Alarm State Change",
                "detail": {"state": {"value": "ALARM"}},
            },
            "alarmName ausente",
        ),
    ],
)
def test_evento_malformado_e_ignorado_sem_excecao(ambiente, evento, fragmento):
    s3, cloudwatch, chamadas_proibidas = ambiente

    resposta = handler_module.lambda_handler(evento, FakeContext())

    assert resposta["status"] == "IGNORADO"
    assert fragmento in resposta["motivo"]
    assert s3.objects == []
    assert cloudwatch.metrics == []
    assert chamadas_proibidas == []


def test_alarme_de_outro_lab_e_ignorado(ambiente):
    s3, _, _ = ambiente
    evento = evento_valido(alarm_name=ALARME_DE_OUTRO_LAB)

    resposta = handler_module.lambda_handler(evento, FakeContext())

    assert resposta["status"] == "IGNORADO"
    assert "não é nenhum dos dois alarmes deste trabalho final" in resposta["motivo"]
    assert s3.objects == []


def test_volta_para_ok_nao_abre_incidente(ambiente):
    """O EventBridge filtra por ALARM, mas a função não pode depender só disso."""
    s3, _, _ = ambiente
    evento = evento_valido(
        detail={"state": {"value": "OK", "reason": "voltou ao normal"}}
    )

    resposta = handler_module.lambda_handler(evento, FakeContext())

    assert resposta["status"] == "IGNORADO"
    assert "não é ALARM" in resposta["motivo"]
    assert s3.objects == []


# --------------------------------------------------------------------------- #
# configuração ausente falha alto (não é evento inválido, é erro de infra)
# --------------------------------------------------------------------------- #


def test_variavel_de_ambiente_obrigatoria_ausente_falha_alto(ambiente, monkeypatch):
    monkeypatch.delenv("LAB_BUCKET")

    with pytest.raises(RuntimeError, match="LAB_BUCKET"):
        handler_module.lambda_handler(evento_valido(), FakeContext())
