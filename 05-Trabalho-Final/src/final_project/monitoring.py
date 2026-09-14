"""Leitura de alarme, incidente e dashboard -- a camada de observabilidade do
trabalho final que so LE o que a infraestrutura (terraform, A6a) e a Lambda
de reacao ja produziram. Nenhuma funcao aqui cria alarme, regra ou
dashboard -- isso e `.tf`, dono A6a.

Publicacao de metrica de drift vive em `drift.py`; este modulo so consulta o
que a AWS registrou a partir dessa publicacao.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import serving
from .aws import AwsError, client, list_objects, log, wait_for
from .config import Config


def _find_alarm(cfg: Config, name_fragment: str) -> str:
    """Descobre o nome real do alarme pelo prefixo do trabalho final e um
    fragmento do nome logico (`data-drift`/`prediction-drift`).

    Nao existe terraform output para o nome do alarme (so os 11 outputs
    congelados por A0 em `outputs.tf`) -- o nome carrega um sufixo aleatorio
    por ciclo de vida (`random_id.lifecycle`) que so o state do Terraform
    conhece. Descobrir pela API em vez de reconstruir o nome evita depender
    de um output novo que nao existe.
    """
    response = client("cloudwatch", cfg.region).describe_alarms(AlarmNamePrefix=cfg.prefix)
    candidates = [
        alarm["AlarmName"] for alarm in response.get("MetricAlarms", []) if name_fragment in alarm["AlarmName"]
    ]
    if not candidates:
        raise AwsError(
            f"nenhum alarme com prefixo {cfg.prefix!r} e fragmento {name_fragment!r} encontrado. "
            "Rode `make apply` (estagio de serving) antes deste comando."
        )
    if len(candidates) > 1:
        raise AwsError(f"mais de um alarme casou com {name_fragment!r}: {candidates}. Nome ambiguo.")
    return candidates[0]


def _alarm_state(alarm_name: str, region: str) -> dict[str, Any]:
    response = client("cloudwatch", region).describe_alarms(AlarmNames=[alarm_name])
    alarms = response.get("MetricAlarms", [])
    if not alarms:
        raise AwsError(f"alarme {alarm_name!r} nao encontrado.")
    alarm = alarms[0]
    return {
        "alarm_name": alarm["AlarmName"],
        "state": alarm["StateValue"],
        "reason": alarm.get("StateReason", ""),
        "metric_name": alarm.get("MetricName"),
        "threshold": alarm.get("Threshold"),
        "updated_at": alarm.get("StateUpdatedTimestamp").isoformat()
        if alarm.get("StateUpdatedTimestamp")
        else None,
    }


def _recent_transitions(alarm_name: str, region: str, max_records: int = 10) -> list[dict[str, str]]:
    """Historico oficial de transicao de estado (auditoria da propria AWS) --
    evidencia que nao depende de o polling deste comando ter chegado no
    instante exato da transicao. Sem isso, uma consulta tardia (o alarme ja
    voltou a OK por falta de novo datapoint) esconderia que ele passou por
    ALARM de verdade."""
    response = client("cloudwatch", region).describe_alarm_history(
        AlarmName=alarm_name, HistoryItemType="StateUpdate", MaxRecords=max_records
    )
    return [
        {"at": item["Timestamp"].isoformat(), "summary": item["HistorySummary"]}
        for item in response.get("AlarmHistoryItems", [])
    ]


def _wait_for_transition(alarm_name: str, region: str, from_state: str, timeout_seconds: int) -> dict[str, Any]:
    """Espera o alarme sair do estado observado em `from_state` -- a prova real
    de que o `PutMetricData` da janela com desvio foi de fato avaliado pelo
    CloudWatch (nao so publicado). Se `from_state` ja for INSUFFICIENT_DATA,
    qualquer saida dali (OK ou ALARM) conta como transicao."""
    return wait_for(
        lambda: _alarm_state(alarm_name, region),
        lambda estado: estado["state"] != from_state,
        timeout_seconds=timeout_seconds,
        description=f"alarme {alarm_name} saindo de {from_state}",
    )


def cmd_alarm_status(cfg: Config, args: Any) -> int:
    data_drift_alarm = _find_alarm(cfg, "data-drift")
    prediction_drift_alarm = _find_alarm(cfg, "prediction-drift")
    watched = {"data_drift": data_drift_alarm, "prediction_drift": prediction_drift_alarm}

    # Snapshot "antes" -- registra o estado no instante em que o comando comeca
    # a rodar (tipicamente OK, herdado da janela baseline ou do estado inicial
    # da infraestrutura), para o `alarm.json` deixar visivel a transicao real
    # provocada pela janela com desvio, nao so o estado final.
    before = {name: _alarm_state(alarm_name, cfg.region) for name, alarm_name in watched.items()}
    for name, estado in before.items():
        log(f"[alarme] antes: {name}: {estado['state']} ({estado['reason']})")

    # Espera cada alarme sair do estado capturado em `before` -- com timeout e
    # backoff (via `aws.wait_for`), nao um unico `describe_alarms` que so
    # fotografaria o estado anterior a avaliacao do novo datapoint.
    after = {
        name: _wait_for_transition(alarm_name, cfg.region, before[name]["state"], timeout_seconds=300)
        for name, alarm_name in watched.items()
    }
    for name, estado in after.items():
        log(f"[alarme] depois: {name}: {estado['state']} ({estado['reason']})")

    history = {name: _recent_transitions(alarm_name, cfg.region) for name, alarm_name in watched.items()}

    alarms = {
        name: {
            **after[name],
            "state_before": before[name]["state"],
            "observed_before_at": before[name]["updated_at"],
            "recent_transitions": history[name],
        }
        for name in watched
    }

    payload = {"alarms": alarms}
    evidence_path = cfg.evidence_dir / "alarm.json"
    evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    log(f"[alarme] evidencia em {evidence_path}")

    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


# --------------------------------------------------------------------------- #
# Reacao -- incidente gravado pela Lambda em S3 (prefixo `incidents/`, ver
# `terraform/locals.tf` local.s3_prefixes.incidents; nao promovido a output).
# --------------------------------------------------------------------------- #

_INCIDENTS_PREFIX = "incidents/"


def _most_recent_incident(bucket: str, region: str) -> dict[str, Any] | None:
    objects = list_objects(bucket, _INCIDENTS_PREFIX, region)
    keys = [obj for obj in objects if obj.get("Key", "") != _INCIDENTS_PREFIX]
    if not keys:
        return None
    return max(keys, key=lambda obj: obj["LastModified"])


def cmd_reaction(cfg: Config, args: Any) -> int:
    outputs = serving.terraform_outputs(cfg)
    bucket = serving.require_output(outputs, "bucket_name")

    incident_object = wait_for(
        lambda: _most_recent_incident(bucket, cfg.region),
        lambda obj: obj is not None,
        timeout_seconds=300,
        description=f"incidente em s3://{bucket}/{_INCIDENTS_PREFIX}",
    )
    key = incident_object["Key"]
    log(f"[reacao] incidente encontrado em s3://{bucket}/{key}")

    body = client("s3", cfg.region).get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    try:
        incident_payload = json.loads(body)
    except json.JSONDecodeError:
        # Schema do payload da Lambda ainda nao esta congelado neste momento
        # (A6a escreve `lambda/drift_response.py` em paralelo) -- registrar o
        # corpo cru em vez de falhar evita que uma divergencia de formato
        # derrube a evidencia inteira.
        incident_payload = {"raw_body": body}

    payload = {
        "bucket": bucket,
        "key": key,
        "last_modified": incident_object["LastModified"].isoformat(),
        "size_bytes": incident_object.get("Size"),
        "incident_payload": incident_payload,
        "found_at": datetime.now(timezone.utc).isoformat(),
    }

    evidence_path = cfg.evidence_dir / "reaction.json"
    evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    log(f"[reacao] evidencia em {evidence_path}")

    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #


def cmd_dashboard(cfg: Config, args: Any) -> int:
    outputs = serving.terraform_outputs(cfg)
    dashboard_name = serving.require_output(outputs, "dashboard_name")
    dashboard_url = serving.require_output(outputs, "dashboard_url")

    try:
        response = client("cloudwatch", cfg.region).get_dashboard(DashboardName=dashboard_name)
        widgets = json.loads(response["DashboardBody"]).get("widgets", [])
        widget_count = len(widgets)
    except Exception as exc:  # noqa: BLE001 - a evidencia registra a falha, nao mascara
        raise AwsError(f"dashboard {dashboard_name!r} nao pode ser lido pela API: {exc}") from exc

    log(f"[dashboard] {dashboard_name}: {widget_count} widget(s)")

    payload = {
        "dashboard_name": dashboard_name,
        "dashboard_url": dashboard_url,
        "widget_count": widget_count,
    }
    evidence_path = cfg.evidence_dir / "dashboard.json"
    evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    log(f"[dashboard] evidencia em {evidence_path}")

    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0
