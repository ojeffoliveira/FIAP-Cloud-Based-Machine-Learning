#!/usr/bin/env python3
"""Portão de entrada do Lab 04.2 — obrigatório antes de qualquer chamada AWS.

Confere ferramentas, credencial/região/role, ausência de recurso órfão,
espaço em disco para o GGUF e conectividade com o Hugging Face Hub. Imprime
`[PASS]`/`[FAIL]` em português; falha nunca com traceback (credencial ausente
ou expirada tem mensagem didática com o passo de renovação do Academy).

Uso: python scripts/doctor.py
Saída: stdout = relatório estruturado (JSON); stderr = progresso.
Exit code: 0 se todos os checks passaram, 1 caso contrário.
"""

from __future__ import annotations

import re
import shutil
import socket
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab42 import aws
from lab42.aws import LabError, emit, log

PASS, FAIL = "[PASS]", "[FAIL]"

# GGUF Q4_0/Q4_K_M do modelo alvo (ex.: Qwen2.5-0.5B) fica na faixa de algumas
# centenas de MB; o piso aqui é generoso para cobrir V1+V2 baixados em
# paralelo mais a folga do container local, sem exigir número exato de um
# modelo que o A7 ainda vai escolher.
_DISCO_MINIMO_GB = 2.0


class Checks:
    """Coletor de verificações — mesmo padrão do 04.1 (`check` é endereço, `detail` é frase)."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.items.append({"check": name, "passed": passed, "detail": detail})
        marker = PASS if passed else FAIL
        log(f"{marker} {name}: {detail}")

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


def _required_terraform_version() -> str | None:
    """Lê required_version dos .tf do lab (runner + slm), se já existirem.

    O doctor roda antes de A5/A6 escreverem `terraform/*/versions.tf` — por
    isso tolera ausência: nesse caso só reporta a versão instalada, sem
    comparar contra nada.
    """
    for stack_dir in (aws.TERRAFORM_SLM_DIR, aws.TERRAFORM_RUNNER_DIR):
        versions_tf = stack_dir / "versions.tf"
        if not versions_tf.exists():
            continue
        match = re.search(
            r'required_version\s*=\s*"=\s*([0-9]+\.[0-9]+\.[0-9]+)"',
            versions_tf.read_text(encoding="utf-8"),
        )
        if match:
            return match.group(1)
    return None


def _installed_terraform_version() -> str | None:
    binary = shutil.which("terraform")
    if not binary:
        return None
    # subprocess só invoca aqui o binário terraform já resolvido via PATH pelo
    # shutil.which acima: argv é lista fixa, sem input do usuário, sem
    # shell=True — os alertas de shell injection do bandit não se aplicam.
    import subprocess  # nosec B404

    result = subprocess.run(  # nosec
        [binary, "version", "-json"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return None
    import json

    try:
        return json.loads(result.stdout)["terraform_version"]
    except (json.JSONDecodeError, KeyError):
        return None


def _terraform_supports_native_lockfile(version: str | None) -> bool:
    """S3 backend `use_lockfile` (lock nativo, sem DynamoDB) existe a partir do 1.10."""
    if version is None:
        return False
    try:
        major, minor = (int(part) for part in version.split(".")[:2])
    except ValueError:
        return False
    return (major, minor) >= (1, 10)


def _disk_livre_gb(path: Path) -> float:
    uso = shutil.disk_usage(path)
    return uso.free / (1024**3)


def main() -> int:
    checks = Checks()
    cfg = aws.load_config()
    region_esperada = cfg["region"]

    major, minor = sys.version_info[:2]
    checks.add(
        "python_version",
        (major, minor) >= (3, 11),
        f"Python {major}.{minor} (o lab pede 3.11 ou mais novo)",
    )

    venv_python = aws.LAB_ROOT / ".venv" / "bin" / "python"
    checks.add(
        "venv",
        venv_python.exists(),
        f"{venv_python} presente"
        if venv_python.exists()
        else "rode `make setup` antes do doctor",
    )

    try:
        import boto3  # noqa: F401
        import yaml  # noqa: F401

        checks.add("dependencias_python", True, "boto3 e PyYAML importáveis")
    except ImportError as exc:
        checks.add(
            "dependencias_python",
            False,
            f"dependência ausente: {exc}. Rode `make setup`.",
        )

    instalada = _installed_terraform_version()
    if instalada is None:
        checks.add("terraform_presente", False, "terraform não encontrado no PATH")
    else:
        checks.add("terraform_presente", True, f"terraform {instalada}")

    exigida = _required_terraform_version()
    if instalada and exigida:
        checks.add(
            "terraform_version",
            instalada == exigida,
            f"instalado {instalada}, versions.tf exige exatamente {exigida}",
        )
    elif instalada:
        checks.add(
            "terraform_version",
            True,
            f"instalado {instalada}; nenhum versions.tf encontrado ainda para comparar",
        )

    if instalada:
        suporta_lock_nativo = _terraform_supports_native_lockfile(instalada)
        checks.add(
            "terraform_lockfile_nativo_s3",
            True,
            f"{'suporta' if suporta_lock_nativo else 'NÃO suporta'} `use_lockfile` no backend S3 "
            f"(requer >= 1.10; instalado {instalada})",
        )

    checks.add(
        "aws_cli_presente",
        shutil.which("aws") is not None,
        "aws CLI encontrada no PATH"
        if shutil.which("aws")
        else "aws CLI ausente do PATH",
    )

    try:
        identity = aws.caller_identity()
        checks.add(
            "aws_credentials",
            True,
            f"conta {identity['Account']} (nenhuma credencial é impressa)",
        )
    except LabError as exc:
        checks.add("aws_credentials", False, str(exc))
        emit(checks.summary("doctor"))
        return 1

    sessao = aws.client("sts").meta.region_name
    checks.add(
        "aws_region",
        sessao == region_esperada,
        f"sessão em {sessao}, o lab exige {region_esperada}"
        + (
            ""
            if sessao == region_esperada
            else " — exporte AWS_DEFAULT_REGION=us-east-1"
        ),
    )

    try:
        arn = aws.role_arn(cfg["aws"]["execution_role_name"])
        checks.add("lab_role", True, arn)
    except LabError as exc:
        checks.add("lab_role", False, str(exc))

    try:
        profile_arn = aws.instance_profile_arn(cfg["aws"]["instance_profile_name"])
        checks.add("lab_instance_profile", True, profile_arn)
    except LabError as exc:
        checks.add("lab_instance_profile", False, str(exc))

    # Alcançabilidade de serviço: uma chamada de leitura barata por serviço que
    # o lab usa. Falha aqui é permissão ou região, e é melhor descobrir agora
    # que no meio de um apply.
    sondas: list[tuple[str, Callable[[], Any], str]] = [
        (
            "sagemaker_reachable",
            lambda: aws.client("sagemaker").list_endpoints(MaxResults=1),
            "ListEndpoints respondeu",
        ),
        (
            "s3_reachable",
            lambda: aws.client("s3").list_buckets(),
            "ListBuckets respondeu",
        ),
        (
            "cloudwatch_reachable",
            lambda: aws.client("cloudwatch").list_dashboards(),
            "ListDashboards respondeu",
        ),
        (
            "application_autoscaling_reachable",
            lambda: aws.client("application-autoscaling").describe_scalable_targets(
                ServiceNamespace="sagemaker"
            ),
            "DescribeScalableTargets respondeu",
        ),
        (
            "ec2_reachable",
            lambda: aws.client("ec2").describe_regions(RegionNames=[region_esperada]),
            "DescribeRegions respondeu",
        ),
    ]
    for nome, sonda, ok_detalhe in sondas:
        try:
            sonda()
            checks.add(nome, True, ok_detalhe)
        except Exception as exc:  # noqa: BLE001 - qualquer falha aqui é diagnóstico
            checks.add(nome, False, f"{type(exc).__name__}: {exc}")

    # Recurso órfão: mesma prova por API que o verify-clean, mas aqui é um
    # aviso de ENTRADA (algo sobrou de uma execução anterior), não de saída.
    try:
        prefixo = cfg["aws"]["bucket_prefix"]
        endpoints_orfaos = [
            e["EndpointName"]
            for e in aws.client("sagemaker")
            .list_endpoints(NameContains=prefixo, MaxResults=100)
            .get("Endpoints", [])
        ]
        checks.add(
            "sem_recurso_orfao",
            not endpoints_orfaos,
            "nenhum endpoint do lab já em pé"
            if not endpoints_orfaos
            else f"AINDA COBRANDO de execução anterior: {', '.join(endpoints_orfaos)} — "
            "rode `make destroy-models` antes de continuar",
        )
    except Exception as exc:  # noqa: BLE001
        checks.add("sem_recurso_orfao", False, f"não foi possível verificar: {exc}")

    livre_gb = _disk_livre_gb(aws.LAB_ROOT)
    checks.add(
        "espaco_em_disco",
        livre_gb >= _DISCO_MINIMO_GB,
        f"{livre_gb:.1f} GB livres (mínimo {_DISCO_MINIMO_GB:.1f} GB para o GGUF em /tmp)",
    )

    try:
        socket.create_connection(("huggingface.co", 443), timeout=5).close()
        checks.add("conectividade_huggingface", True, "huggingface.co:443 respondeu")
    except OSError as exc:
        checks.add(
            "conectividade_huggingface",
            False,
            f"sem conectividade com huggingface.co: {exc}",
        )

    resultado = checks.summary("doctor")
    emit(resultado)
    return 0 if resultado["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LabError as exc:
        log(f"[FAIL] {exc}")
        sys.exit(1)
