#!/usr/bin/env python3
"""Status ponta a ponta do runner self-hosted do GitHub Actions (Lab 04.2).

Não decide nada, só relata: consulta a instância EC2, o SSM e (se `gh` estiver
autenticado) o registro do runner no GitHub, e imprime um veredito [PASS]/
[FAIL] por item. Usado depois de `terraform apply` em terraform/runner/ e
depois de `scripts/register_runner.sh`, para confirmar que a cadeia inteira
(infra -> agente -> registro) está de fato como o lab exige — nunca por
inferência, sempre por chamada de API real.

Uso: python scripts/runner_status.py
Saída: stdout = relatório estruturado (JSON); stderr = progresso.
Exit code: 0 se todos os checks passaram, 1 caso contrário.
"""

from __future__ import annotations

import shutil

# só chama `gh api` com argv fixo, nunca shell nem input do usuário
import subprocess  # nosec B404
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab42 import aws
from lab42.aws import LabError, emit, log

PASS, FAIL = "[PASS]", "[FAIL]"


class Checks:
    """Mesmo padrão de scripts/doctor.py: `check` é endereço, `detail` é frase."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.items.append({"check": name, "passed": passed, "detail": detail})
        log(f"{PASS if passed else FAIL} {name}: {detail}")

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [item for item in self.items if not item["passed"]]

    def summary(self, title: str) -> dict[str, Any]:
        ok = not self.failed
        log("")
        log(
            f"{PASS if ok else FAIL} {title}: "
            f"{len(self.items) - len(self.failed)}/{len(self.items)} verificações passaram"
        )
        return {
            "passed": ok,
            "checks_total": len(self.items),
            "checks_failed": len(self.failed),
            "checks": self.items,
        }


def _instance_id() -> str:
    """Sempre lê o instance-id do output real do Terraform — nunca hardcoded.

    Se o apply de terraform/runner ainda não rodou, isso falha com LabError
    explicando o passo que falta, em vez de um traceback do subprocess.
    """
    outputs = aws.terraform_outputs(aws.TERRAFORM_RUNNER_DIR)
    instance_id = outputs.get("instance_id")
    if not instance_id:
        raise LabError(
            "terraform/runner ainda não tem output `instance_id`. Rode `terraform apply` "
            "em terraform/runner/ antes de checar o status do runner."
        )
    return str(instance_id)


def _describe_instance(instance_id: str) -> dict[str, Any]:
    reservations = aws.client("ec2").describe_instances(InstanceIds=[instance_id])[
        "Reservations"
    ]
    if not reservations or not reservations[0]["Instances"]:
        raise LabError(
            f"instância {instance_id} não encontrada na conta/região atual — foi destruída?"
        )
    return reservations[0]["Instances"][0]


def _security_group_inbound_rule_count(group_id: str) -> int:
    groups = aws.client("ec2").describe_security_groups(GroupIds=[group_id])[
        "SecurityGroups"
    ]
    return len(groups[0]["IpPermissions"]) if groups else -1


def _ssm_ping(instance_id: str) -> dict[str, Any] | None:
    info = aws.client("ssm").describe_instance_information(
        Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
    )["InstanceInformationList"]
    return info[0] if info else None


def _provisioning_marker(instance_id: str, timeout_s: int = 30) -> tuple[bool, str]:
    """Confirma /opt/actions-runner/.provisionado via SSM — nunca assume pelo tempo de boot.

    send_command roda como root na instância (SSM Session Manager já opera
    assim); o conteúdo devolvido é só a timestamp que o user-data grava, nunca
    nada sensível.
    """
    ssm = aws.client("ssm")
    command_id = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": [
                "cat /opt/actions-runner/.provisionado 2>/dev/null || echo AUSENTE",
                (
                    "/opt/actions-runner/bin/Runner.Listener --version 2>/dev/null "
                    "|| echo VERSAO_INDISPONIVEL"
                ),
            ]
        },
    )["Command"]["CommandId"]

    waiter_config = {"Delay": 2, "MaxAttempts": timeout_s // 2}
    try:
        aws.client("ssm").get_waiter("command_executed").wait(
            CommandId=command_id, InstanceId=instance_id, WaiterConfig=waiter_config
        )
    except Exception as exc:  # noqa: BLE001 — o waiter falha para Failed/TimedOut; lemos o invocation abaixo mesmo assim
        log(
            f"aviso: waiter do comando SSM não confirmou sucesso ({exc}); lendo invocation mesmo assim"
        )

    invocation = ssm.get_command_invocation(
        CommandId=command_id, InstanceId=instance_id
    )
    output = invocation.get("StandardOutputContent", "")
    lines = [line for line in output.splitlines() if line.strip()]
    marker_line = lines[0] if lines else ""
    present = marker_line != "AUSENTE" and marker_line != ""
    return present, marker_line


def _gh_runner_state(owner: str, repo: str, label: str) -> dict[str, Any] | None:
    """Consulta o registro do runner via `gh api` — degrada sem falhar se `gh` não estiver logado.

    O lab não trata `gh` autenticado como pré-requisito de infraestrutura (é
    uma conveniência do aluno rodando localmente/no Codespaces); por isso este
    check nunca lança LabError, só reporta ausência.
    """
    if not shutil.which("gh"):
        return None
    # "gh" vem do PATH de propósito (mesmo binário do `gh auth login` do aluno);
    # argv é lista fixa, sem shell=True, sem interpolar input externo — só
    # aws.github_owner()/lab.yaml.
    result = subprocess.run(  # nosec B603 B607
        ["gh", "api", f"repos/{owner}/{repo}/actions/runners", "--paginate"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    import json

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    runners = payload.get("runners", [])
    for runner in runners:
        labels = {item["name"] for item in runner.get("labels", [])}
        if label in labels:
            return runner
    return {"_not_found_with_label": label, "_runners_seen": len(runners)}


def main() -> int:
    checks = Checks()
    cfg = aws.load_config()
    runner_cfg = cfg.get("runner", {})
    expected_label = runner_cfg.get("github_actions_label", "academy-slm-deploy")

    try:
        instance_id = _instance_id()
    except LabError as exc:
        checks.add("terraform_output_instance_id", False, str(exc))
        emit(checks.summary("runner_status"))
        return 1
    checks.add("terraform_output_instance_id", True, instance_id)

    log(f"Consultando EC2 describe-instances para {instance_id}...")
    try:
        instance = _describe_instance(instance_id)
    except LabError as exc:
        checks.add("ec2_describe_instances", False, str(exc))
        emit(checks.summary("runner_status"))
        return 1

    state = instance["State"]["Name"]
    checks.add("ec2_state_running", state == "running", f"estado atual: {state}")

    expected_type = runner_cfg.get("instance_type", "t3.medium")
    actual_type = instance.get("InstanceType", "")
    checks.add(
        "ec2_instance_type",
        actual_type == expected_type,
        f"tipo {actual_type} (esperado {expected_type})",
    )

    az = instance.get("Placement", {}).get("AvailabilityZone", "")
    public_ip = instance.get("PublicIpAddress", "")
    checks.add(
        "ec2_public_ip",
        bool(public_ip),
        f"IP público: {public_ip or 'ausente'} (AZ {az})",
    )

    profile_arn = instance.get("IamInstanceProfile", {}).get("Arn", "")
    expected_profile = cfg["aws"]["instance_profile_name"]
    checks.add(
        "ec2_instance_profile",
        profile_arn.endswith((f"/{expected_profile}", f":{expected_profile}")),
        f"instance profile anexado: {profile_arn or 'nenhum'} (esperado {expected_profile})",
    )

    metadata_options = instance.get("MetadataOptions", {})
    expected_imds = runner_cfg.get("imds", {})
    imds_ok = (
        metadata_options.get("HttpTokens")
        == expected_imds.get("http_tokens", "required")
        and metadata_options.get("HttpPutResponseHopLimit")
        == expected_imds.get("http_put_response_hop_limit", 1)
        and metadata_options.get("HttpEndpoint")
        == expected_imds.get("http_endpoint", "enabled")
    )
    checks.add(
        "ec2_imdsv2_enforced",
        imds_ok,
        f"HttpTokens={metadata_options.get('HttpTokens')}, "
        f"HttpPutResponseHopLimit={metadata_options.get('HttpPutResponseHopLimit')}, "
        f"HttpEndpoint={metadata_options.get('HttpEndpoint')}",
    )

    sg_ids = [sg["GroupId"] for sg in instance.get("SecurityGroups", [])]
    inbound_total = 0
    for sg_id in sg_ids:
        count = _security_group_inbound_rule_count(sg_id)
        inbound_total += max(count, 0)
    checks.add(
        "security_group_zero_inbound",
        inbound_total == 0,
        f"{inbound_total} regra(s) inbound somando {len(sg_ids)} SG(s) ({', '.join(sg_ids)})",
    )

    log("Consultando SSM describe-instance-information...")
    ssm_info = _ssm_ping(instance_id)
    if ssm_info is None:
        checks.add(
            "ssm_online",
            False,
            "instância não aparece em describe-instance-information",
        )
    else:
        ping_status = ssm_info.get("PingStatus", "")
        launch_time = instance.get("LaunchTime")
        last_ping = ssm_info.get("LastPingDateTime")
        latency = ""
        if isinstance(launch_time, datetime) and isinstance(last_ping, datetime):
            delta = last_ping - launch_time
            latency = f"; primeiro ping {delta.total_seconds():.0f}s após o LaunchTime"
        checks.add(
            "ssm_online",
            ping_status == "Online",
            f"PingStatus={ping_status}{latency}",
        )

    log("Enviando comando SSM para checar o marcador de provisionamento...")
    try:
        marker_present, marker_detail = _provisioning_marker(instance_id)
        checks.add(
            "provisioning_marker",
            marker_present,
            f"/opt/actions-runner/.provisionado: {marker_detail or 'vazio'}",
        )
    except Exception as exc:  # noqa: BLE001 — SSM indisponível não deve virar traceback
        checks.add(
            "provisioning_marker", False, f"não foi possível checar via SSM: {exc}"
        )

    log("Consultando o registro do runner no GitHub via `gh api` (se autenticado)...")
    try:
        owner = aws.github_owner()
    except LabError as exc:
        checks.add(
            "github_runner_registered",
            False,
            f"não foi possível resolver o owner: {exc}",
        )
        owner = None

    if owner is not None:
        repo = aws.LAB_ROOT.parents[
            1
        ].name  # nome do repositório clonado (funciona em qualquer fork)
        gh_state = _gh_runner_state(owner, repo, expected_label)
        if gh_state is None:
            checks.add(
                "github_runner_registered",
                False,
                "`gh` não instalado/autenticado — pulei este check (não é pré-requisito de infra). "
                "Rode `gh auth login` para habilitá-lo.",
            )
        elif "_not_found_with_label" in gh_state:
            checks.add(
                "github_runner_registered",
                False,
                f"nenhum runner com a label {expected_label!r} encontrado "
                f"({gh_state['_runners_seen']} runner(s) no repositório)",
            )
        else:
            online = gh_state.get("status") == "online"
            busy = gh_state.get("busy", False)
            labels = [item["name"] for item in gh_state.get("labels", [])]
            checks.add(
                "github_runner_registered",
                online,
                f"status={gh_state.get('status')}, busy={busy}, "
                f"ephemeral={gh_state.get('ephemeral')}, labels={labels}",
            )

    result = checks.summary("runner_status")
    result["instance_id"] = instance_id
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    emit(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
