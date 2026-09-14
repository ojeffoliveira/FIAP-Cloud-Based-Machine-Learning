"""`package` e `validate-package` — o único entregável do trabalho final.

`cmd_package` monta `trabalho-final-cloud-ml.zip` a partir de uma lista
explícita de origens (nunca um `zip -r` cru do repositório): é essa lista
explícita, e não um filtro de exclusão por cima de tudo, que garante que
`.venv/`, `.terraform/`, state, credencial etc. simplesmente nunca chegam a
ser candidatos a entrar no pacote. O escâner de segredo e a lista de
proibidos (`_FORBIDDEN_*`) são a rede de segurança que roda por cima dessa
seleção — devem dar zero ocorrência por construção; se acharem alguma coisa,
é sinal de que um arquivo indevido foi colocado dentro de um diretório
incluído, e o pacote FALHA (nunca sanitiza silenciosamente).

`cmd_package` se recusa estruturalmente a rodar sem
`artifacts/evidence/cleanup.json` com `passed: true` — a ordem
`check -> destroy -> verify-clean -> evidence -> package -> validate-package`
de `make finish` só é real se isto for garantido em código, não só em
documentação.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .aws import log
from .config import Config
from .evidence import DECISION_PLACEHOLDER, evaluate_completeness
from .solution import SolutionError, load_and_validate, solution_path

ZIP_ROOT_NAME = "trabalho-final-cloud-ml"
ZIP_FILENAME = "trabalho-final-cloud-ml.zip"

# Exatamente a lista de `06_DELIVERY_ZIP_CONTRACT.md` §3 — o que precisa
# existir dentro de `evidence/` no pacote. `packaging.py` copia todo o
# conteúdo de `artifacts/evidence/` (então `candidates.json`/`candidates.md`,
# exigidos por `evidence.cmd_check` mas fora desta lista específica, também
# viajam como bônus) — esta tupla é o piso mínimo que `validate-package`
# confere, não um teto.
ZIP_REQUIRED_EVIDENCE_FILES: tuple[str, ...] = (
    "data-contract.json",
    "training.json",
    "artifact.json",
    "solution.json",
    "serving.json",
    "atendimento.json",
    "campanha.json",
    "baseline-drift.json",
    "production-drift.json",
    "alarm.json",
    "reaction.json",
    "quality.json",
    "dashboard.json",
    "cleanup.json",
    "manifest.json",
    "evidence.md",
)

# Diretórios que nunca são candidatos a entrar no pacote, em qualquer
# subárvore copiada (src/, scripts/, lambda/, terraform/).
_EXCLUDE_DIR_NAMES = {"__pycache__", ".terraform", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", ".git", ".aws"}

# Arquivos que nunca são candidatos, por sufixo ou por nome exato — espelha
# `06_DELIVERY_ZIP_CONTRACT.md` §4 mais o `.gitignore` do próprio projeto.
_EXCLUDE_FILE_SUFFIXES = (".pyc", ".pyo", ".pyd", ".tfstate", ".tfplan", ".log", ".pem", ".ppk")
_EXCLUDE_FILE_NAMES_EXACT = {"artifact.auto.tfvars.json", "terraform.tfvars", "credentials"}
_EXCLUDE_FILE_PREFIXES = ("tfplan", "crash.")

# Extensões de artefato binário de modelo — nenhuma pertence ao repositório
# entregue (o modelo vive só no S3/SageMaker), então a presença de qualquer
# uma é sinal de erro, não algo a filtrar em silêncio.
_MODEL_BINARY_SUFFIXES = (".pkl", ".joblib", ".h5", ".onnx", ".pb", ".bin", ".tar.gz", ".whl")

_BINARY_SCAN_SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".pdf")

# --------------------------------------------------------------------------- #
# Escâner de segredo — nunca imprime o valor casado, só a categoria e o arquivo.
# --------------------------------------------------------------------------- #

_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA|AGPA|ANPA|ANVA|ASCA)[A-Z0-9]{16}\b")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |)PRIVATE KEY-----")

# Estes dois exigem um VALOR plausível depois do marcador (não só o nome da
# chave) — de outro modo o próprio código que define estes padrões (este
# arquivo, que precisa mencionar "aws_session_token"/"SecretAccessKey" como
# string para poder procurá-los) sempre daria falso positivo em si mesmo.
_SESSION_TOKEN_RE = re.compile(
    r'aws_session_token\s*=\s*\S{10,}|"SessionToken"\s*:\s*"[^"]{10,}"|X-Amz-Security-Token:\s*\S{10,}',
    re.IGNORECASE,
)
_SECRET_KEY_RE = re.compile(
    r'aws_secret_access_key\s*=\s*\S{10,}|"SecretAccessKey"\s*:\s*"[^"]{10,}"',
    re.IGNORECASE,
)


def _scan_secrets(path: Path, relative: str) -> list[str]:
    """Devolve uma lista de achados (categoria, nunca o valor) para `path`.
    Arquivos binários conhecidos (imagens, o próprio zip) não são varridos —
    decodificar PNG como texto só geraria ruído.

    Deliberadamente NÃO trata o account id da AWS como segredo: ele não
    autentica nada por si só, e `cleanup.json`/`manifest.json` — ambos
    arquivos de evidência obrigatórios — precisam registrá-lo em texto
    claro como proveniência da verificação (qual conta foi checada). O
    `SUBMISSION.md` já mostra a conta mascarada para o resumo voltado a
    humanos; aqui a evidência crua fica intacta."""
    if path.suffix.lower() in _BINARY_SCAN_SKIP_SUFFIXES:
        return []
    try:
        text = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return []

    findings: list[str] = []
    if _ACCESS_KEY_RE.search(text):
        findings.append(f"{relative}: padrão de AWS access key id encontrado")
    if _PRIVATE_KEY_RE.search(text):
        findings.append(f"{relative}: cabeçalho de chave privada encontrado")
    if _SESSION_TOKEN_RE.search(text):
        findings.append(f"{relative}: marcador de session token da AWS (com valor) encontrado")
    if _SECRET_KEY_RE.search(text):
        findings.append(f"{relative}: marcador de secret access key da AWS (com valor) encontrado")
    return findings


def _is_forbidden(relative: str) -> str | None:
    """Devolve o motivo se `relative` (caminho relativo à raiz do pacote)
    bater em algum padrão proibido; `None` se estiver limpo."""
    parts = Path(relative).parts
    if any(part in _EXCLUDE_DIR_NAMES for part in parts):
        return "diretório proibido no caminho"
    name = parts[-1]
    if name in _EXCLUDE_FILE_NAMES_EXACT:
        return "nome de arquivo proibido (credencial/handoff gerado)"
    if any(name.startswith(prefix) for prefix in _EXCLUDE_FILE_PREFIXES):
        return "prefixo de arquivo proibido (plano/crash do Terraform)"
    if any(name.endswith(suffix) for suffix in _EXCLUDE_FILE_SUFFIXES):
        return "sufixo de arquivo proibido (state/plano/cache/log/credencial)"
    if any(name.endswith(suffix) for suffix in _MODEL_BINARY_SUFFIXES):
        return "extensão de artefato binário de modelo — o modelo vive no S3/SageMaker, não no repositório"
    if ".aws" in parts:
        return "diretório .aws/ proibido"
    return None


# --------------------------------------------------------------------------- #
# Seleção explícita do que entra no pacote
# --------------------------------------------------------------------------- #


def _walk_tree(root: Path) -> list[Path]:
    """Lista arquivos de `root` já pulando os diretórios proibidos — não
    depende do escâner posterior para nunca sequer descer em `.terraform/`
    ou `__pycache__/`."""
    found: list[Path] = []
    if not root.exists():
        return found
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if any(part in _EXCLUDE_DIR_NAMES for part in path.relative_to(root).parts):
            continue
        found.append(path)
    return found


def _collect_entries(cfg: Config) -> list[tuple[Path, str]]:
    """Devolve `(caminho_no_disco, caminho_relativo_dentro_do_pacote)` para
    tudo que vai para o zip, ANTES de qualquer nome de `trabalho-final-cloud-ml/`.
    A raiz do pacote nunca é `cfg.root` inteiro — é esta lista fechada."""
    entries: list[tuple[Path, str]] = []

    for name in ("Makefile", "requirements.txt"):
        src = cfg.root / name
        if src.exists():
            entries.append((src, name))

    for name in ("config/scenario.yaml", "config/acceptance.yaml"):
        src = cfg.root / name
        if src.exists():
            entries.append((src, name))

    for name in ("student/solution.yaml", "student/DECISION.md"):
        src = cfg.root / name
        if src.exists():
            entries.append((src, name))

    for subdir in ("src", "scripts", "lambda", "terraform"):
        base = cfg.root / subdir
        for path in _walk_tree(base):
            relative = str(Path(subdir) / path.relative_to(base))
            entries.append((path, relative))

    arquitetura = cfg.root / "diagramas" / "arquitetura.png"
    if arquitetura.exists():
        entries.append((arquitetura, "diagramas/arquitetura.png"))

    for path in _walk_tree(cfg.evidence_dir):
        entries.append((path, f"evidence/{path.name}"))

    return entries


# --------------------------------------------------------------------------- #
# Precondições de `package`
# --------------------------------------------------------------------------- #


def _check_cleanup_pass(cfg: Config) -> tuple[bool, str, dict[str, Any] | None]:
    """A recusa estrutural: sem `cleanup.json` com `passed: true`, `package`
    nem chega a olhar o resto. `verify-clean` (cleanup.py) é quem grava esse
    arquivo, sempre por consulta direta à API — nunca por leitura de state."""
    path = cfg.evidence_dir / "cleanup.json"
    if not path.exists():
        return False, f"{path} não existe — rode `verify-clean` antes de `package`.", None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"{path} não é um JSON válido: {exc}", None
    if not isinstance(data, dict) or data.get("passed") is not True:
        return False, f"{path} não registra `passed: true` — rode `verify-clean` de novo depois de corrigir o que sobrou.", data if isinstance(data, dict) else None
    return True, f"{path}: passed=true ({data.get('checks_total', '?')} verificações)", data


def _check_solution(cfg: Config) -> tuple[bool, str]:
    try:
        sol = load_and_validate(cfg)
    except SolutionError as exc:
        return False, str(exc)
    return True, f"patterns preenchidos (atendimento={sol.atendimento_pattern}, campanha={sol.campanha_pattern})"


def _check_decision(cfg: Config) -> tuple[bool, str]:
    path = cfg.root / "student" / "DECISION.md"
    if not path.exists():
        return False, f"{path} não encontrado"
    pendentes = path.read_text(encoding="utf-8").count(DECISION_PLACEHOLDER)
    if pendentes:
        return False, f"{pendentes} ocorrência(s) de `{DECISION_PLACEHOLDER}` ainda não preenchidas em {path}"
    return True, "sem placeholder pendente"


def _check_evidence_complete(cfg: Config) -> tuple[bool, str]:
    checks, _ = evaluate_completeness(cfg)
    resultado = checks.summary("package:evidence")
    if resultado["passed"]:
        return True, f"{resultado['checks_total']} verificações de evidência passaram"
    detalhes = "; ".join(item["check"] for item in checks.failed)
    return False, f"evidência incompleta: {detalhes}"


def _check_forbidden(entries: list[tuple[Path, str]]) -> tuple[bool, str]:
    achados = [f"{relative} ({motivo})" for _, relative in entries if (motivo := _is_forbidden(relative))]
    if achados:
        return False, f"{len(achados)} arquivo(s) proibido(s) selecionado(s) para o pacote: {achados}"
    return True, "nenhum arquivo proibido na seleção do pacote"


def _check_secrets(entries: list[tuple[Path, str]]) -> tuple[bool, str]:
    achados: list[str] = []
    for path, relative in entries:
        achados.extend(_scan_secrets(path, relative))
    if achados:
        # Nunca o valor casado — só a categoria e o arquivo, para o aluno
        # conseguir localizar e remover sem a mensagem de erro se tornar
        # ela mesma um vazamento.
        return False, f"{len(achados)} possível(is) segredo(s) encontrado(s): {achados}"
    return True, "nenhum padrão de segredo encontrado na seleção do pacote"


# --------------------------------------------------------------------------- #
# SUBMISSION.md
# --------------------------------------------------------------------------- #


def _mask(value: str, keep_start: int = 3, keep_end: int = 2) -> str:
    if not value or len(value) <= keep_start + keep_end:
        return "*" * len(value)
    return value[:keep_start] + "*" * (len(value) - keep_start - keep_end) + value[-keep_end:]


def _build_submission_md(cfg: Config, manifest: dict[str, Any], cleanup: dict[str, Any] | None, sol_group: str, sol_members: list[str]) -> str:
    lines: list[str] = []
    lines.append("# SUBMISSION")
    lines.append("")
    lines.append(
        "Gerado automaticamente por `make package`. Não contém recomendação nem conclusão sobre "
        "qual pattern de serving foi escolhido — isso está em `student/DECISION.md`."
    )
    lines.append("")
    lines.append(f"- Equipe: {sol_group or 'n/d'} ({', '.join(sol_members) if sol_members else 'n/d'})")
    lines.append(f"- Gerado em: `{datetime.now(timezone.utc).isoformat(timespec='seconds')}`")
    lines.append(f"- Commit: `{manifest.get('git_commit', 'desconhecido')}`")
    lines.append(f"- Região AWS: `{manifest.get('region', cfg.region)}`")
    lines.append(f"- Patterns escolhidos: atendimento=`{manifest.get('patterns', {}).get('atendimento', 'n/d')}`, campanha=`{manifest.get('patterns', {}).get('campanha', 'n/d')}`")
    account = str(manifest.get("account", ""))
    lines.append(f"- Conta AWS (mascarada): `{_mask(account)}`")
    lines.append("")
    lines.append("## Principais arquivos e hashes")
    lines.append("")
    lines.append("| arquivo | sha256 |")
    lines.append("|---|---|")
    for name, info in sorted(manifest.get("files", {}).items()):
        lines.append(f"| `evidence/{name}` | `{info['sha256']}` |")
    lines.append("")
    lines.append("## Resultado do verify-clean")
    lines.append("")
    if cleanup:
        lines.append(f"- `passed`: `{cleanup.get('passed')}`")
        lines.append(f"- verificações: {cleanup.get('checks_total', '?')} total, {cleanup.get('checks_failed', '?')} falharam")
        lines.append(f"- consultado em: `{cleanup.get('checked_at', 'n/d')}`")
    lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# cmd_package
# --------------------------------------------------------------------------- #


def cmd_package(cfg: Config, args: Any) -> int:
    log("[package] verificando precondições antes de tocar em qualquer zip")

    cleanup_ok, cleanup_detail, cleanup_data = _check_cleanup_pass(cfg)
    log(f"{'[OK]' if cleanup_ok else '[FALHA]'} verify-clean: {cleanup_detail}")
    if not cleanup_ok:
        return 1

    solution_ok, solution_detail = _check_solution(cfg)
    log(f"{'[OK]' if solution_ok else '[FALHA]'} solution.yaml: {solution_detail}")
    if not solution_ok:
        return 1

    decision_ok, decision_detail = _check_decision(cfg)
    log(f"{'[OK]' if decision_ok else '[FALHA]'} DECISION.md: {decision_detail}")
    if not decision_ok:
        return 1

    evidence_ok, evidence_detail = _check_evidence_complete(cfg)
    log(f"{'[OK]' if evidence_ok else '[FALHA]'} evidência: {evidence_detail}")
    if not evidence_ok:
        return 1

    manifest_path = cfg.evidence_dir / "manifest.json"
    if not manifest_path.exists():
        log(f"[FALHA] {manifest_path} não existe — rode `evidence` depois de `verify-clean` antes de `package`.")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    entries = _collect_entries(cfg)

    forbidden_ok, forbidden_detail = _check_forbidden(entries)
    log(f"{'[OK]' if forbidden_ok else '[FALHA]'} arquivos proibidos: {forbidden_detail}")
    if not forbidden_ok:
        return 1

    secrets_ok, secrets_detail = _check_secrets(entries)
    log(f"{'[OK]' if secrets_ok else '[FALHA]'} segredos: {secrets_detail}")
    if not secrets_ok:
        return 1

    sol_path = solution_path(cfg)
    sol_raw = yaml.safe_load(sol_path.read_text(encoding="utf-8"))
    authors = sol_raw.get("authors", {}) if isinstance(sol_raw, dict) else {}

    submission_md = _build_submission_md(
        cfg,
        manifest,
        cleanup_data,
        str(authors.get("group", "")),
        [str(m) for m in authors.get("members", [])] if isinstance(authors.get("members"), list) else [],
    )

    zip_path = cfg.root / ZIP_FILENAME
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
        tmp.write(submission_md.encode("utf-8"))
        submission_tmp_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for disk_path, relative in entries:
                zf.write(disk_path, f"{ZIP_ROOT_NAME}/{relative}")
            zf.write(submission_tmp_path, f"{ZIP_ROOT_NAME}/SUBMISSION.md")
    finally:
        submission_tmp_path.unlink(missing_ok=True)

    log(f"[package] gravado {zip_path} com {len(entries) + 1} arquivo(s)")

    resultado = {
        "passed": True,
        "zip_path": str(zip_path),
        "entry_count": len(entries) + 1,
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
    }
    print(json.dumps(resultado, indent=2, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# cmd_validate_package
# --------------------------------------------------------------------------- #


def _print_result(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"[PASS] {label}")
    else:
        print(f"[FAIL] {label}")
    if detail:
        log(f"  -> {detail}")


def cmd_validate_package(cfg: Config, args: Any) -> int:
    """Descompacta em `/tmp`, confere estrutura/solução/decisão/evidência/
    segurança/cleanup e imprime o formato literal de
    `06_DELIVERY_ZIP_CONTRACT.md` §6 — sete linhas em stdout, na ordem."""
    zip_path = cfg.root / ZIP_FILENAME
    if not zip_path.exists():
        log(f"[FALHA] {zip_path} não existe — rode `package` antes de `validate-package`.")
        _print_result("estrutura", False, f"{zip_path} não existe")
        return 1

    extract_dir = Path(tempfile.mkdtemp(prefix="tf-final-validate-"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
        package_root = extract_dir / ZIP_ROOT_NAME

        overall_ok = True

        # 1. estrutura: raiz + arquivos/diretórios obrigatórios.
        estrutura_missing = []
        if not package_root.is_dir():
            estrutura_missing.append(f"pasta raiz {ZIP_ROOT_NAME}/ ausente")
        else:
            for required in (
                "Makefile",
                "requirements.txt",
                "config/scenario.yaml",
                "config/acceptance.yaml",
                "student/solution.yaml",
                "student/DECISION.md",
                "src/final_project",
                "scripts",
                "lambda",
                "terraform",
                "diagramas/arquitetura.png",
                "evidence",
                "SUBMISSION.md",
            ):
                if not (package_root / required).exists():
                    estrutura_missing.append(required)
            for required_evidence in ZIP_REQUIRED_EVIDENCE_FILES:
                if not (package_root / "evidence" / required_evidence).exists():
                    estrutura_missing.append(f"evidence/{required_evidence}")
        estrutura_ok = not estrutura_missing
        overall_ok &= estrutura_ok
        _print_result("estrutura", estrutura_ok, "faltando: " + ", ".join(estrutura_missing) if estrutura_missing else "completa")

        # 2. solução: YAML parseia e não tem TODO.
        solucao_ok = False
        solucao_detail = ""
        solution_file = package_root / "student" / "solution.yaml"
        if solution_file.exists():
            try:
                raw = yaml.safe_load(solution_file.read_text(encoding="utf-8"))
                text_upper = solution_file.read_text(encoding="utf-8").upper()
                solucao_ok = isinstance(raw, dict) and "TODO" not in text_upper
                solucao_detail = "sem TODO" if solucao_ok else "TODO pendente ou YAML inválido"
            except yaml.YAMLError as exc:
                solucao_detail = f"YAML inválido: {exc}"
        else:
            solucao_detail = "student/solution.yaml ausente no pacote"
        overall_ok &= solucao_ok
        _print_result("solução", solucao_ok, solucao_detail)

        # 3. decisão: sem placeholder.
        decisao_ok = False
        decision_file = package_root / "student" / "DECISION.md"
        if decision_file.exists():
            pendentes = decision_file.read_text(encoding="utf-8").count(DECISION_PLACEHOLDER)
            decisao_ok = pendentes == 0
            decisao_detail = "sem placeholder pendente" if decisao_ok else f"{pendentes} placeholder(s) pendente(s)"
        else:
            decisao_detail = "student/DECISION.md ausente no pacote"
        overall_ok &= decisao_ok
        _print_result("decisão", decisao_ok, decisao_detail)

        # 4. evidência: todo JSON parseia, manifest consistente, hashes batem.
        evidencia_ok, evidencia_detail = _validate_evidence_dir(package_root / "evidence")
        overall_ok &= evidencia_ok
        _print_result("evidência", evidencia_ok, evidencia_detail)

        # 5. segurança: sem credencial, sem arquivo proibido, no CONTEÚDO do zip.
        seguranca_ok, seguranca_detail = _validate_security(package_root)
        overall_ok &= seguranca_ok
        _print_result("segurança", seguranca_ok, seguranca_detail)

        # 6. cleanup: passed == true.
        cleanup_file = package_root / "evidence" / "cleanup.json"
        cleanup_ok = False
        cleanup_detail = ""
        if cleanup_file.exists():
            try:
                cleanup_data = json.loads(cleanup_file.read_text(encoding="utf-8"))
                cleanup_ok = isinstance(cleanup_data, dict) and cleanup_data.get("passed") is True
                cleanup_detail = "passed=true" if cleanup_ok else "passed != true"
            except json.JSONDecodeError as exc:
                cleanup_detail = f"JSON inválido: {exc}"
        else:
            cleanup_detail = "evidence/cleanup.json ausente"
        overall_ok &= cleanup_ok
        _print_result("cleanup", cleanup_ok, cleanup_detail)

        # 7. veredito final.
        if overall_ok:
            print("[PASS] pacote pronto para o portal FIAP")
        else:
            print("[FAIL] pacote não está pronto para o portal FIAP")
        return 0 if overall_ok else 1
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def _validate_evidence_dir(evidence_dir: Path) -> tuple[bool, str]:
    if not evidence_dir.is_dir():
        return False, "evidence/ ausente no pacote"

    problemas: list[str] = []
    manifest_path = evidence_dir / "manifest.json"
    manifest: dict[str, Any] | None = None
    for path in sorted(evidence_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problemas.append(f"{path.name}: JSON inválido ({exc})")
            continue
        if path.name == "manifest.json" and isinstance(data, dict):
            manifest = data

    if manifest is None:
        problemas.append("manifest.json ausente ou inválido")
    else:
        for name, info in manifest.get("files", {}).items():
            candidate = evidence_dir / name
            if not candidate.exists():
                problemas.append(f"manifest cita {name}, ausente no pacote")
                continue
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            expected = info.get("sha256") if isinstance(info, dict) else None
            if expected and actual != expected:
                problemas.append(f"{name}: hash não bate com manifest.json")

    if problemas:
        return False, "; ".join(problemas)
    return True, "todo JSON parseou, manifest consistente com os hashes"


def _validate_security(package_root: Path) -> tuple[bool, str]:
    problemas: list[str] = []
    for path in package_root.rglob("*"):
        if path.is_dir():
            continue
        relative = str(path.relative_to(package_root))
        motivo = _is_forbidden(relative)
        if motivo:
            problemas.append(f"{relative} ({motivo})")
            continue
        problemas.extend(_scan_secrets(path, relative))

    if problemas:
        return False, f"{len(problemas)} problema(s): {problemas}"
    return True, "nenhum arquivo proibido ou segredo no conteúdo do pacote"


if __name__ == "__main__":
    from .config import load_config

    sys.exit(cmd_package(load_config(), None))
