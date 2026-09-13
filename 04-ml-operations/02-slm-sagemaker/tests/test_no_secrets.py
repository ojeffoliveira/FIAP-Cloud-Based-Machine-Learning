"""Testes que reprovam qualquer segredo AWS ou binário de modelo no Lab 04.2.

Cobre a regra do threat model "GitHub nunca recebe access key AWS" (spec
`05_GITHUB_ACTIONS_SECURITY.md` §8) e a garantia de supply chain de que o
GGUF/tarball do modelo nunca é versionado (`09_IMPLEMENTATION_BLUEPRINT.md`
§18, reforçado por `/tmp/cloud-code-lab04-2-a7/spec-no-binarios.md`).

Os testes de binário (`test_no_binary_extensions_*`) seguem exatamente a
especificação do agente A7 — `git ls-files` para o que está rastreado e
`rglob` para o que está na árvore de trabalho, mesmo sem commit.
"""

import json
import pathlib
import re
import subprocess

import pytest

LAB_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = LAB_ROOT.parents[1]

BINARY_EXTENSIONS = (".gguf", ".tar.gz", ".safetensors")

# Diretórios que nunca precisam ser varridos por segredo/texto: são gerados,
# de ferramenta, ou (no caso de .git) já cobertos pelo próprio git ls-files.
SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".terraform",
    "node_modules",
}


def _e_virtualenv(nome: str) -> bool:
    """Qualquer virtualenv, não só `.venv`.

    O CI cria `.venv-ci` e `.venv-test` (árvores separadas por causa do conflito
    entre checkov e boto3), e dependências de terceiros lá dentro disparam falso
    positivo: binários em `dateutil/zoneinfo`, strings de credencial em
    `botocore/data` e nomes como AWS_SECRET_ACCESS_KEY em `botocore/credentials`.
    Casar por prefixo evita ter que listar nome de venv um a um.
    """
    return nome.startswith((".venv", "venv"))


# AKIA = access key de longo prazo; ASIA = access key temporária (STS) —
# as duas nunca deveriam aparecer em texto neste lab, que só usa
# LabInstanceProfile/IMDSv2 (spec §8).
AWS_ACCESS_KEY_ID_RE = re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")
# Secret access key: 40 chars base64-like atribuído a um nome que denuncia
# a intenção (evita falso positivo em qualquer string aleatória de 40
# chars, como um SHA-256 truncado ou hash de teste).
AWS_SECRET_KEY_RE = re.compile(
    r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}"
)
AWS_SESSION_TOKEN_RE = re.compile(
    r"(?i)aws_session_token\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{100,}"
)
CREDENTIAL_ENV_NAMES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)

REQUIRED_GITIGNORE_ENTRIES = (
    # Lista literal de 09_IMPLEMENTATION_BLUEPRINT.md §18...
    ".venv/",
    ".generated/",
    "artifacts/",
    "*.gguf",
    "*.tar.gz",
    ".terraform/",
    "*.tfstate",
    "*.tfstate.*",
    "*.tfplan",
    "crash.log",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".bandit/",
    ".scannerwork/",
    ".DS_Store",
    # ...mais *.safetensors: o .gitignore precisa ser pelo menos tão amplo
    # quanto os testes de binário desta suíte (ver spec-no-binarios.md do
    # agente A7, seção 1), senão git status fica sujo antes do CI rodar.
    "*.safetensors",
)


def _iter_text_files():
    for path in LAB_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(parte in SKIP_DIR_NAMES or _e_virtualenv(parte) for parte in path.parts):
            continue
        if path.suffix in {".gguf", ".tar.gz", ".safetensors", ".png", ".svg", ".pdf"}:
            continue
        yield path


def test_no_binary_extensions_tracked_by_git():
    """Reprova se qualquer .gguf/.tar.gz/.safetensors estiver no índice do
    git — pega inclusive o caso de um binário já commitado e depois
    "esquecido" no .gitignore, que só checar o .gitignore não detectaria."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=LAB_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = result.stdout.splitlines()
    offending = [f for f in tracked if f.endswith(BINARY_EXTENSIONS)]
    assert not offending, (
        f"Binário de modelo rastreado pelo git: {offending}. GGUF/tar.gz/"
        "safetensors nunca entram no repositório."
    )


def test_no_binary_extensions_in_working_tree():
    """Reprova se o binário existir na árvore de trabalho, mesmo sem
    estar staged/commitado — pega o caso de model_sync.py apontar para
    dentro do repo por engano, antes mesmo de um `git add`.

    Ignora `.venv/` e afins (mesma lista de SKIP_DIR_NAMES usada na
    varredura de segredos): um dev local que roda `make setup` tem
    dependências como python-dateutil empacotando `.tar.gz` dentro do
    próprio virtualenv — isso já é coberto pelo `.gitignore` (`.venv/`) e
    não é o risco que este teste existe para pegar.
    """
    offending = [
        str(p.relative_to(LAB_ROOT))
        for ext in BINARY_EXTENSIONS
        for p in LAB_ROOT.rglob(f"*{ext}")
        if not any(
            parte in SKIP_DIR_NAMES or _e_virtualenv(parte)
            for parte in p.relative_to(LAB_ROOT).parts
        )
    ]
    assert not offending, (
        "Binário de modelo presente na árvore de trabalho do lab (não "
        f"commitado, mas presente no disco): {offending}."
    )


def test_no_aws_static_credentials_in_text_files():
    """Reprova qualquer padrão de access key (AKIA/ASIA), secret key ou
    session token em texto claro em qualquer arquivo do lab — é a garantia
    de que 'GitHub nunca recebe access key AWS' vale para o próprio
    repositório, não só para os workflows."""
    achados = []
    for path in _iter_text_files():
        try:
            conteudo = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if AWS_ACCESS_KEY_ID_RE.search(conteudo):
            achados.append(
                (str(path.relative_to(LAB_ROOT)), "access key id (AKIA/ASIA)")
            )
        if AWS_SECRET_KEY_RE.search(conteudo):
            achados.append((str(path.relative_to(LAB_ROOT)), "aws_secret_access_key"))
        if AWS_SESSION_TOKEN_RE.search(conteudo):
            achados.append((str(path.relative_to(LAB_ROOT)), "aws_session_token"))
    assert not achados, f"Credencial AWS em texto claro encontrada: {achados}"


def test_no_static_aws_credential_env_names_as_assignments():
    """Reprova atribuição/exportação literal de AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY ou AWS_SESSION_TOKEN em qualquer script/config do
    lab — a única fonte de credencial permitida é o IMDSv2/
    LabInstanceProfile do runner self-hosted (nunca variável estática)."""
    padrao = re.compile(
        r"(?:export\s+)?(" + "|".join(CREDENTIAL_ENV_NAMES) + r")\s*=\s*['\"]?[^\s'\"]+"
    )
    achados = []
    for path in _iter_text_files():
        try:
            conteudo = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in padrao.finditer(conteudo):
            achados.append((str(path.relative_to(LAB_ROOT)), match.group(1)))
    assert not achados, (
        f"Atribuição literal de variável de credencial AWS estática encontrada: {achados}"
    )


def test_no_versioned_aws_credentials_file():
    """Reprova a existência de um arquivo `.aws/credentials` rastreado
    pelo git — é o formato canônico de credencial de longo prazo da AWS
    CLI e nunca deveria estar dentro do repositório."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = result.stdout.splitlines()
    achados = [
        f for f in tracked if f.endswith(".aws/credentials") or "/.aws/credentials" in f
    ]
    assert not achados, f".aws/credentials rastreado pelo git: {achados}"


def test_gitignore_contem_entradas_obrigatorias():
    """Reprova se o `.gitignore` do lab não cobrir todas as entradas
    exigidas por 09_IMPLEMENTATION_BLUEPRINT.md §18 — sem isso, um
    `git add .` distraído versiona artefato/binário/state do Terraform."""
    gitignore = LAB_ROOT / ".gitignore"
    if not gitignore.exists():
        pytest.fail(f"{gitignore} ausente — obrigatório pela spec §18")
    linhas = {
        linha.strip() for linha in gitignore.read_text(encoding="utf-8").splitlines()
    }
    faltando = [
        entrada for entrada in REQUIRED_GITIGNORE_ENTRIES if entrada not in linhas
    ]
    assert not faltando, (
        f".gitignore do lab não cobre entradas obrigatórias: {faltando}"
    )


def _iter_evidence_files():
    evidence_dir = LAB_ROOT / "artifacts" / "evidence"
    if not evidence_dir.exists():
        pytest.skip(
            "artifacts/evidence/ ainda não existe — só é gerado por uma "
            "execução real do pipeline (make evidence / deploy-v2), não "
            "faz parte do código versionado do lab."
        )
    yield from evidence_dir.glob("*.json")


SUSPECT_EVIDENCE_KEYS = (
    "access_key",
    "access_key_id",
    "secret_key",
    "secret_access_key",
    "session_token",
    "password",
    "private_key",
)


def _chaves_suspeitas(obj, prefixo=""):
    """Gera os caminhos (dotted) de chaves que parecem credencial dentro
    de um documento JSON já carregado — função de módulo (não fecha sobre
    variável de loop) para evitar ambiguidade de escopo em chamada
    recursiva."""
    if isinstance(obj, dict):
        for chave, valor in obj.items():
            caminho = f"{prefixo}.{chave}" if prefixo else chave
            if any(suspeita in chave.lower() for suspeita in SUSPECT_EVIDENCE_KEYS):
                yield caminho
            yield from _chaves_suspeitas(valor, caminho)
    elif isinstance(obj, list):
        for item in obj:
            yield from _chaves_suspeitas(item, prefixo)


def test_evidence_json_sem_campo_de_credencial():
    """Reprova qualquer evidence JSON com chave que pareça credencial —
    09_IMPLEMENTATION_BLUEPRINT.md §11 define exatamente quais campos o
    credential-source proof pode conter (account, arn_type,
    credential_method, static_env_keys_present) e nenhum deles é segredo;
    qualquer chave adicional do tipo *_key/*_token/password é regressão."""
    achados = []
    for path in _iter_evidence_files():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for caminho in _chaves_suspeitas(doc):
            achados.append((str(path.relative_to(LAB_ROOT)), caminho))
    assert not achados, f"Campo de credencial encontrado em evidence JSON: {achados}"


def test_workflows_do_lab_sem_secrets_aws_referenciados():
    """Reprova, nos próprios workflows do Lab 04.2, qualquer referência a
    `secrets.AWS_*` — reforço específico deste arquivo de testes para o
    caso de alguém adicionar um secret AWS ao repositório GitHub e
    referenciá-lo do workflow, contornando a ausência de env estático."""
    workflows_dir = REPO_ROOT / ".github" / "workflows"
    achados = []
    for nome in ("04-2-quality.yml", "04-2-deploy-v2.yml"):
        caminho = workflows_dir / nome
        if not caminho.exists():
            continue
        conteudo = caminho.read_text(encoding="utf-8")
        if re.search(r"secrets\.AWS_", conteudo):
            achados.append(nome)
    assert not achados, f"Workflow(s) referenciando secrets.AWS_*: {achados}"
