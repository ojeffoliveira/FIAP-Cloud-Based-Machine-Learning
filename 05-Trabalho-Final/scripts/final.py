#!/usr/bin/env python3
"""Ponto único de entrada do Trabalho Final — `python scripts/final.py <subcomando>`.

Ordem em que o Makefile encadeia os subcomandos ao longo do trabalho:

    doctor -> data -> validate-data -> validate-solution -> train
           -> training-status -> artifact -> status -> compare
           -> atendimento -> campanha -> baseline -> drift -> alarm-status
           -> reaction -> ground-truth -> dashboard -> evidence -> resumo
           -> check -> verify-clean -> package -> validate-package

Disciplina de saída, valendo para todo subcomando: **stdout carrega o
resultado** (o JSON que alguém vai capturar ou pipar), **stderr carrega
progresso** (via `log`, nunca `print` direto).

Este arquivo é só o dispatcher. `doctor` e `check` são implementados aqui;
todos os outros subcomandos chamam uma função `cmd_<nome>(cfg, args) -> int`
de um módulo de `src/final_project/` que outro agente está escrevendo em
paralelo agora. Por isso os módulos de negócio (dataset, training, serving,
drift, evidence, ...) são importados **sob demanda, dentro do handler** —
nunca no topo do arquivo: um `import` no topo travaria `--help` e todo o
resto do CLI enquanto um único módulo não existisse. `config`, `aws` e
`solution` já foram entregues (onda 0) e por isso são importados no topo como
qualquer outra dependência estável.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# aws.py importa boto3/botocore e config.py importa PyYAML: se o ambiente
# ainda não tiver `pip install -r requirements.txt` rodado, um import direto
# no topo derrubaria `--help` com um ModuleNotFoundError cru. Guardado aqui
# para que essa ausência vire um item de `doctor`, não uma tela de traceback.
try:
    from final_project import aws  # noqa: E402
except Exception as exc:  # noqa: BLE001 - qualquer falha de import é diagnóstico de ambiente
    aws = None  # type: ignore[assignment]
    _AWS_IMPORT_ERROR: Exception | None = exc
else:
    _AWS_IMPORT_ERROR = None

try:
    from final_project import config as config_mod  # noqa: E402
except Exception as exc:  # noqa: BLE001
    config_mod = None  # type: ignore[assignment]
    _CONFIG_IMPORT_ERROR: Exception | None = exc
else:
    _CONFIG_IMPORT_ERROR = None

try:
    from final_project import solution as solution_mod  # noqa: E402
except Exception as exc:  # noqa: BLE001
    solution_mod = None  # type: ignore[assignment]
    _SOLUTION_IMPORT_ERROR: Exception | None = exc
else:
    _SOLUTION_IMPORT_ERROR = None


PASS, FAIL = "[OK]", "[FALHA]"


class DispatchError(RuntimeError):
    """Erro do próprio dispatcher: subcomando indisponível ou config ausente.

    Mensagem já pronta para o aluno ler — quem chama nunca deixa isto vazar
    como traceback.
    """


def log(*args: Any) -> None:
    """Progresso para stderr. Usa `aws.log` quando disponível; senão cai para
    um `print` equivalente — importante para o próprio `doctor` conseguir
    relatar que `aws.py` não importou, sem travar por falta do helper."""
    if aws is not None:
        aws.log(*args)
    else:
        print(*args, file=sys.stderr, flush=True)


def emit(payload: dict[str, Any]) -> None:
    """Único ponto de saída em stdout: o resultado que alguém captura ou pipa."""
    print(json.dumps(payload, indent=2, ensure_ascii=False))


class Checks:
    """Coletor de verificações no formato que o aluno lê no terminal.

    O nome do check não é traduzido: é um endereço que o aluno cola de volta
    para quem for ajudar a debugar. O detalhe ao lado é frase, e por isso é
    em português.
    """

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


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def _installed_terraform_version() -> str | None:
    binary = shutil.which("terraform")
    if binary is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - binário resolvido via PATH, sem shell
            [binary, "version", "-json"], capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)["terraform_version"]
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        return None


def _required_terraform_version() -> str | None:
    """Lê `required_version` de `terraform/versions.tf` em vez de embutir o
    número aqui: o pin fica só num lugar, o próprio `.tf` que o `terraform
    init` de fato usa."""
    versions_tf = Path(__file__).resolve().parents[1] / "terraform" / "versions.tf"
    if not versions_tf.exists():
        return None
    match = re.search(
        r'required_version\s*=\s*"=\s*([0-9]+\.[0-9]+\.[0-9]+)"',
        versions_tf.read_text(encoding="utf-8"),
    )
    return match.group(1) if match else None


def cmd_doctor(cfg: Any, args: argparse.Namespace) -> int:
    """Checagem de ambiente. Roda mesmo se `config`/`aws` não importaram —
    é justamente o `doctor` que precisa contar isso ao aluno."""
    checks = Checks()

    major, minor = sys.version_info[:2]
    checks.add(
        "python_version",
        (major, minor) >= (3, 11),
        f"Python {major}.{minor} (o trabalho final pede 3.11 ou mais novo)",
    )

    venv_ativo = bool(os.environ.get("VIRTUAL_ENV")) or sys.prefix != sys.base_prefix
    checks.add(
        "venv_ativo",
        venv_ativo,
        "ambiente virtual ativo"
        if venv_ativo
        else "nenhum venv ativo — rode scripts/setup.sh e ative-o (`source .venv/bin/activate`) antes de continuar",
    )

    if _AWS_IMPORT_ERROR is None:
        checks.add("boto3_importavel", True, "boto3/botocore importados com sucesso")
    else:
        checks.add(
            "boto3_importavel",
            False,
            f"não foi possível importar boto3/botocore ({_AWS_IMPORT_ERROR}). "
            "Rode scripts/setup.sh para instalar requirements.txt no ambiente ativo.",
        )

    terraform_bin = shutil.which("terraform")
    checks.add(
        "terraform_no_path",
        terraform_bin is not None,
        terraform_bin or "terraform não encontrado no PATH. Instale o Terraform e reabra o terminal.",
    )

    instalada = _installed_terraform_version()
    exigida = _required_terraform_version()
    if instalada is None:
        checks.add("terraform_version", False, "não foi possível executar `terraform version`")
    elif exigida is None:
        checks.add(
            "terraform_version", False, "não foi possível ler required_version de terraform/versions.tf"
        )
    else:
        checks.add(
            "terraform_version",
            instalada == exigida,
            f"instalado {instalada}, terraform/versions.tf exige exatamente {exigida}",
        )

    region = None
    if cfg is not None:
        region = cfg.region
    if region is None:
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    checks.add("regiao_configurada", True, f"região usada nesta checagem: {region}")

    if aws is None:
        checks.add(
            "aws_credentials",
            False,
            f"módulo `final_project.aws` não pôde ser carregado ({_AWS_IMPORT_ERROR}).",
        )
    else:
        try:
            identity = aws.check_credentials(region)
            checks.add(
                "aws_credentials",
                True,
                f"conta {identity['account']} (nenhuma credencial é impressa)",
            )
        except aws.AwsError as exc:
            checks.add("aws_credentials", False, str(exc))

    resultado = checks.summary("doctor")
    emit(resultado)
    return 0 if resultado["passed"] else 1


# --------------------------------------------------------------------------- #
# check — verificação pré-`finish`
# --------------------------------------------------------------------------- #


def cmd_check(cfg: Any, args: argparse.Namespace) -> int:
    """`evidence.py` (A7) ganha um `cmd_check` mais completo depois; enquanto
    ele não existir, esta é a versão mínima: solution.yaml sem TODO/pattern
    em branco, DECISION.md sem `<preencher>` e ao menos uma evidência já
    gravada."""
    try:
        evidence_module = importlib.import_module("final_project.evidence")
    except ImportError:
        evidence_module = None
    delegate = getattr(evidence_module, "cmd_check", None) if evidence_module else None

    if callable(delegate):
        log("[check] delegando para final_project.evidence.cmd_check")
        return delegate(cfg, args)

    log("[check] final_project.evidence.cmd_check ainda não existe — rodando verificação mínima")
    checks = Checks()

    if solution_mod is None:
        checks.add(
            "solution_yaml",
            False,
            f"módulo `final_project.solution` não pôde ser carregado ({_SOLUTION_IMPORT_ERROR}).",
        )
    else:
        try:
            sol = solution_mod.load_and_validate(cfg)
            checks.add(
                "solution_yaml",
                True,
                f"patterns preenchidos, grupo {sol.group!r}, {len(sol.members)} integrante(s)",
            )
        except solution_mod.SolutionError as exc:
            checks.add("solution_yaml", False, str(exc))

    decision_path = cfg.root / "student" / "DECISION.md"
    if not decision_path.exists():
        checks.add("decision_md", False, f"{decision_path} não encontrado")
    else:
        pendentes = decision_path.read_text(encoding="utf-8").count("<preencher>")
        checks.add(
            "decision_md",
            pendentes == 0,
            "sem placeholder pendente"
            if pendentes == 0
            else f"{pendentes} ocorrência(s) de `<preencher>` ainda não preenchidas",
        )

    evidence_files = sorted(p.name for p in cfg.evidence_dir.glob("*.json")) if cfg.evidence_dir.exists() else []
    checks.add(
        "evidence_presente",
        bool(evidence_files),
        f"{len(evidence_files)} arquivo(s) em artifacts/evidence/"
        if evidence_files
        else "nenhum arquivo em artifacts/evidence/ — rode `make run` antes de `make finish`",
    )

    resultado = checks.summary("check")
    emit(resultado)
    return 0 if resultado["passed"] else 1


# --------------------------------------------------------------------------- #
# validate-solution — único subcomando que fala direto com solution.py (A0)
# --------------------------------------------------------------------------- #


def cmd_validate_solution(cfg: Any, args: argparse.Namespace) -> int:
    if solution_mod is None:
        raise DispatchError(
            f"módulo `final_project.solution` não pôde ser carregado: {_SOLUTION_IMPORT_ERROR}"
        )
    try:
        sol = solution_mod.load_and_validate(cfg)
    except solution_mod.SolutionError as exc:
        log(f"{FAIL} {exc}")
        return 1

    log(f"{PASS} solution.yaml válido — grupo {sol.group!r}, {len(sol.members)} integrante(s)")
    emit(
        {
            "source_path": str(sol.source_path),
            "atendimento_pattern": sol.atendimento_pattern,
            "campanha_pattern": sol.campanha_pattern,
            "group": sol.group,
            "members": sol.members,
        }
    )
    return 0


# --------------------------------------------------------------------------- #
# Dispatch genérico para os módulos entregues por outros agentes
# --------------------------------------------------------------------------- #


def _lazy(module_name: str, func_name: str, owner: str) -> Callable[[Any, argparse.Namespace], int]:
    """Fábrica de handler: só importa `final_project.<module_name>` quando o
    subcomando é de fato chamado. Ausência do módulo ou da função vira
    `DispatchError` — mensagem clara, nunca traceback cru — e não afeta
    nenhum outro subcomando."""

    def handler(cfg: Any, args: argparse.Namespace) -> int:
        try:
            module = importlib.import_module(f"final_project.{module_name}")
        except ImportError as exc:
            raise DispatchError(
                f"subcomando indisponível: `final_project.{module_name}` (dono: {owner}) "
                f"ainda não foi entregue ({exc}). Tente de novo depois que esse módulo existir."
            ) from exc
        func = getattr(module, func_name, None)
        if not callable(func):
            raise DispatchError(
                f"subcomando indisponível: `final_project.{module_name}.{func_name}` "
                f"ainda não existe (dono: {owner})."
            )
        return func(cfg, args)

    return handler


# name -> (handler, help). Ordem = ordem do ciclo de vida no README.
COMMANDS: dict[str, tuple[Callable[[Any, argparse.Namespace], int], str]] = {
    "doctor": (cmd_doctor, "Confere Python, venv, boto3, terraform, região e credencial AWS"),
    "data": (_lazy("dataset", "cmd_data", "A2"), "Gera o dataset determinístico do cenário"),
    "validate-data": (
        _lazy("data_contract", "cmd_validate_data", "A2"),
        "Roda o contrato de dados executável",
    ),
    "validate-solution": (cmd_validate_solution, "Valida student/solution.yaml e grava a evidência"),
    "train": (_lazy("training", "cmd_train", "A4"), "Dispara o training job"),
    "training-status": (
        _lazy("training", "cmd_training_status", "A4"),
        "Consulta o estado do training job",
    ),
    "artifact": (_lazy("training", "cmd_artifact", "A4"), "Resolve o artefato do modelo treinado"),
    "status": (_lazy("serving", "cmd_status", "A5"), "Descreve o estado dos candidatos de serving"),
    "compare": (_lazy("candidates", "cmd_compare", "A5"), "Produz evidência comparativa dos candidatos"),
    "atendimento": (
        _lazy("workloads", "cmd_atendimento", "A5"),
        "Executa o workload de atendimento com o pattern escolhido pelo grupo",
    ),
    "campanha": (
        _lazy("workloads", "cmd_campanha", "A5"),
        "Executa o workload de campanha com o pattern escolhido pelo grupo",
    ),
    "baseline": (_lazy("drift", "cmd_baseline", "A6"), "Observa a janela saudável"),
    "drift": (_lazy("drift", "cmd_drift", "A6"), "Observa a janela deslocada e publica o drift"),
    "alarm-status": (_lazy("monitoring", "cmd_alarm_status", "A6"), "Mostra o estado dos alarmes"),
    "reaction": (_lazy("monitoring", "cmd_reaction", "A6"), "Valida a reação automática ao incidente"),
    "ground-truth": (
        _lazy("evaluation", "cmd_ground_truth", "A6"),
        "Avalia a qualidade do modelo com o rótulo atrasado",
    ),
    "dashboard": (_lazy("monitoring", "cmd_dashboard", "A6"), "Imprime nome e link do dashboard"),
    "evidence": (_lazy("evidence", "cmd_evidence", "A7"), "Consolida o dossiê de evidência"),
    "resumo": (
        _lazy("evidence", "cmd_resumo", "A7"),
        "Grava a tabela de evidências em student/DECISION.md e repete no terminal",
    ),
    "check": (cmd_check, "Verificação pré-finish do que o aluno entregou"),
    "verify-clean": (_lazy("cleanup", "cmd_verify_clean", "A7"), "Prova por API que nada cobrado sobrou"),
    "package": (_lazy("packaging", "cmd_package", "A7"), "Empacota a entrega final"),
    "validate-package": (
        _lazy("packaging", "cmd_validate_package", "A7"),
        "Valida o pacote de entrega antes do envio",
    ),
}


def _load_config_best_effort() -> Any:
    """Só para `doctor`: nunca levanta — a ausência de config/scenario.yaml
    não pode impedir de ver o resto do diagnóstico de ambiente."""
    if config_mod is None:
        return None
    try:
        return config_mod.load_config()
    except Exception as exc:  # noqa: BLE001 - diagnóstico, não fluxo de controle
        log(f"{FAIL} config: {exc}")
        return None


def _require_config() -> Any:
    if config_mod is None:
        raise DispatchError(
            f"módulo `final_project.config` não pôde ser carregado: {_CONFIG_IMPORT_ERROR}"
        )
    return config_mod.load_config()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="final.py",
        description="Trabalho Final Cloud-Based ML — CLI único, um subcomando por etapa.",
    )
    sub = parser.add_subparsers(dest="comando", required=True)
    for nome, (_, ajuda) in COMMANDS.items():
        sub.add_parser(nome, help=ajuda)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler, _ = COMMANDS[args.comando]

    cfg = _load_config_best_effort() if args.comando == "doctor" else None
    try:
        if args.comando != "doctor":
            cfg = _require_config()
        return handler(cfg, args)
    except RuntimeError as exc:
        # Convenção do repositório: todo erro acionável (DispatchError, AwsError,
        # ConfigError, SolutionError, e o que os demais módulos seguirem) é uma
        # subclasse de RuntimeError com mensagem pronta para o aluno ler.
        log("")
        log(f"{FAIL} {exc}")
        return 1
    except KeyboardInterrupt:
        log("")
        log("[aviso] interrompido pelo usuário.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
