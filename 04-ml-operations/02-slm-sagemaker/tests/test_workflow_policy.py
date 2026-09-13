"""Testes que fazem cumprir a política de segurança dos workflows do Lab 04.2.

Cada teste aqui corresponde a uma regra numerada em
`/tmp/cloud-code-lab04-2-a4/politica-workflows.md` (`G-n` para regras que valem
para os dois workflows, `Q-n` só para `04-2-quality.yml`, `D-n` só para
`04-2-deploy-v2.yml`) e à especificação de teste em
`/tmp/cloud-code-lab04-2-a4/spec-test-workflow-policy.md`. O código/número da
regra aparece dentro da mensagem de assert de cada teste — a falha do teste é,
ela mesma, a documentação executável de qual risco do threat model foi
reintroduzido.

`04-2-deploy-v2.yml` é entregável do agente A10 e pode ainda não existir no
repositório quando esta suíte roda: todo teste `D-n` detecta a ausência e usa
`pytest.skip`, deixando isso explícito no próprio nome da função.
"""

import pathlib
import re

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
QUALITY = REPO_ROOT / ".github" / "workflows" / "04-2-quality.yml"
DEPLOY = REPO_ROOT / ".github" / "workflows" / "04-2-deploy-v2.yml"

FORBIDDEN_TRIGGERS = {"pull_request_target", "issue_comment", "repository_dispatch"}
USES_RE = re.compile(r"uses:\s*([^\s#]+)")
SHA40_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
VERSION_COMMENT_RE = re.compile(r"#\s*v?\d+(?:\.\d+)*")
PINNED_LINE_RE = re.compile(r"uses:\s*[^\s#]+@[0-9a-f]{40}")
BINARY_ARTIFACT_RE = re.compile(r"\.gguf$|\.tar\.gz$", re.IGNORECASE)


def load_raw(path: pathlib.Path) -> str:
    """Texto bruto do workflow, usado pelos testes que precisam de regex
    sobre a linha exata (uses:, comentário de versão)."""
    return path.read_text(encoding="utf-8")


def load_yaml(path: pathlib.Path) -> dict:
    """YAML parseado do workflow, usado pelos testes que precisam de
    estrutura (jobs, permissions, steps)."""
    return yaml.safe_load(load_raw(path))


def get_triggers(doc: dict) -> dict:
    """Retorna o bloco `on:` do workflow.

    PyYAML (resolver YAML 1.1) interpreta a chave literal `on` como a chave
    booleana `True`. Sem este helper, `doc["on"]` levanta `KeyError` mesmo em
    workflows válidos — é a armadilha documentada em
    `politica-workflows.md`.
    """
    if "on" in doc:
        return doc["on"] or {}
    return doc.get(True, {}) or {}


def _skip_if_missing(path: pathlib.Path) -> None:
    if path.exists():
        return
    if path == DEPLOY:
        pytest.skip(
            "04-2-deploy-v2.yml ainda não existe (entregável pendente do "
            "agente A10) — este teste de política será exercido de verdade "
            "quando o workflow de deploy for adicionado ao repositório."
        )
    pytest.fail(f"workflow obrigatório do Lab 04.2 ausente: {path}")


def _iter_run_blocks(doc: dict):
    for nome_job, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps", []) or []:
            if "run" in step:
                yield nome_job, step.get("name", "<step sem nome>"), step["run"]


def _iter_permission_scopes(doc: dict):
    yield "workflow", doc.get("permissions")
    for nome_job, job in (doc.get("jobs") or {}).items():
        if isinstance(job, dict) and "permissions" in job:
            yield f"job:{nome_job}", job["permissions"]


parametrize_workflows = pytest.mark.parametrize(
    "workflow_path", [QUALITY, DEPLOY], ids=["quality", "deploy"]
)


# --------------------------------------------------------------------------
# Regras G — valem para os dois workflows do lab
# --------------------------------------------------------------------------


@parametrize_workflows
def test_g1_actions_pinned_to_full_sha(workflow_path):
    """G1: impede tag mutável (@main/@master/@v4) — o autor da action
    poderia repontar a tag para código malicioso depois que o lab foi
    auditado, sem que o workflow precisasse mudar uma linha."""
    _skip_if_missing(workflow_path)
    usos = USES_RE.findall(load_raw(workflow_path))
    assert usos, f"nenhuma linha 'uses:' encontrada em {workflow_path.name}"
    for valor in usos:
        assert SHA40_RE.fullmatch(valor), (
            f"G1 violada em {workflow_path.name}: '{valor}' não está no "
            "formato owner/repo@sha40 — toda action deve ser pinada por "
            "SHA de commit completo (40 hex), nunca por tag/branch mutável."
        )


@parametrize_workflows
def test_g2_pinned_action_has_version_comment(workflow_path):
    """G2: sem um comentário de versão humana ao lado do SHA, ninguém
    consegue saber qual release aquele commit representa na hora de
    revisar/atualizar a action — a rastreabilidade do pin se perde."""
    _skip_if_missing(workflow_path)
    for linha in load_raw(workflow_path).splitlines():
        if PINNED_LINE_RE.search(linha):
            assert VERSION_COMMENT_RE.search(linha), (
                f"G2 violada em {workflow_path.name}: linha pinada por SHA "
                f"sem comentário de versão humana ao lado: {linha.strip()!r}"
            )


@parametrize_workflows
def test_g3_no_forbidden_triggers(workflow_path):
    """G3: pull_request_target/issue_comment/repository_dispatch rodam com
    o contexto (e possivelmente secrets) do repositório base mesmo para
    código vindo de um fork — é o vetor #2 do threat model deste lab."""
    _skip_if_missing(workflow_path)
    triggers = get_triggers(load_yaml(workflow_path))
    achados = FORBIDDEN_TRIGGERS & set(triggers.keys())
    assert not achados, (
        f"G3 violada em {workflow_path.name}: trigger(s) proibido(s) em "
        f"'on:': {achados} — repo público não pode expor esses gatilhos."
    )


@parametrize_workflows
def test_g4_permissions_contents_read_only(workflow_path):
    """G4: qualquer escopo do GITHUB_TOKEN além de contents:read
    (write/id-token/actions/security-events) amplia o que código
    comprometido em algum step poderia fazer com o token padrão."""
    _skip_if_missing(workflow_path)
    doc = load_yaml(workflow_path)
    permissoes = dict(_iter_permission_scopes(doc))
    workflow_perm = permissoes.get("workflow")
    assert workflow_perm == {"contents": "read"}, (
        f"G4 violada em {workflow_path.name}: 'permissions' no nível do "
        f"workflow deve ser exatamente {{'contents': 'read'}}, encontrado: "
        f"{workflow_perm!r}"
    )
    for local, valor in permissoes.items():
        if local == "workflow":
            continue
        assert valor in ({"contents": "read"}, None), (
            f"G4 violada em {workflow_path.name}: permissions em {local} "
            f"introduz escopo diferente de contents:read: {valor!r}"
        )


@parametrize_workflows
def test_g5_no_context_interpolation_inside_run(workflow_path):
    """G5: interpolar ${{ }} direto dentro de run: injeta a expressão como
    texto de shell antes da execução — qualquer contexto controlado por
    quem abre o PR/dispatch (título, input) vira injeção de comando."""
    _skip_if_missing(workflow_path)
    doc = load_yaml(workflow_path)
    for nome_job, nome_step, bloco in _iter_run_blocks(doc):
        assert "${{" not in bloco, (
            f"G5 violada em {workflow_path.name}, job '{nome_job}', step "
            f"'{nome_step}': run: interpola '${{{{ ... }}}}' direto — o "
            "valor dinâmico deve entrar via env: e ser lido como variável "
            'de shell (ex.: "$VAR").'
        )


@parametrize_workflows
def test_g6_no_dependency_cache(workflow_path):
    """G6: cache de dependências é vetor de cache poisoning — uma execução
    não confiável poderia gravar uma entrada de cache que uma execução
    confiável (push/deploy) depois restauraria sem revalidar."""
    _skip_if_missing(workflow_path)
    doc = load_yaml(workflow_path)
    for nome_job, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps", []) or []:
            uses = step.get("uses", "") or ""
            if uses.startswith("actions/setup-python@"):
                with_bloco = step.get("with") or {}
                assert "cache" not in with_bloco, (
                    f"G6 violada em {workflow_path.name}, job '{nome_job}': "
                    "setup-python não pode declarar with.cache neste lab."
                )
            assert not uses.startswith("actions/cache@"), (
                f"G6 violada em {workflow_path.name}, job '{nome_job}': "
                "actions/cache não pode ser usada neste lab."
            )


@parametrize_workflows
def test_g7_no_binary_or_secret_artifact(workflow_path):
    """G7: subir .gguf/.tar.gz como artifact do Actions é exfiltração (e
    desperdício de storage) — o binário do modelo nunca deveria ficar
    disponível para download a partir de uma execução do workflow."""
    _skip_if_missing(workflow_path)
    doc = load_yaml(workflow_path)
    for nome_job, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps", []) or []:
            uses = step.get("uses", "") or ""
            if not uses.startswith("actions/upload-artifact@"):
                continue
            caminho = (step.get("with") or {}).get("path", "")
            for linha in str(caminho).splitlines():
                assert not BINARY_ARTIFACT_RE.search(linha), (
                    f"G7 violada em {workflow_path.name}, job '{nome_job}': "
                    f"upload-artifact com path que casa com binário de "
                    f"modelo: {linha!r}"
                )


# --------------------------------------------------------------------------
# Regras Q — só 04-2-quality.yml
# --------------------------------------------------------------------------


def test_q1_allowed_triggers_and_scoped_push():
    """Q1: um push irrestrito ao repositório inteiro faria o quality
    workflow (que roda sem revisão humana prévia) disparar para qualquer
    mudança fora do Lab 04.2 — o path precisa ficar restrito ao lab."""
    doc = load_yaml(QUALITY)
    triggers = get_triggers(doc)
    assert set(triggers.keys()) <= {"pull_request", "push"}, (
        "Q1 violada: 'on:' do quality workflow contém trigger fora de "
        f"{{'pull_request', 'push'}}: {set(triggers.keys())}"
    )
    if "push" in triggers:
        push = triggers["push"] or {}
        paths = push.get("paths") or []
        assert paths, (
            "Q1 violada: 'push' sem 'paths' dispara o quality workflow para "
            "qualquer mudança no repositório inteiro, não só no Lab 04.2."
        )
        for caminho in paths:
            assert caminho.startswith(
                ("04-ml-operations/", ".github/workflows/04-2-")
            ), f"Q1 violada: path de push fora do escopo do lab: {caminho!r}"


def test_q2_runs_on_ubuntu_latest_only():
    """Q2: se algum job deste workflow rodasse em self-hosted, código de PR
    de fork não confiável chegaria na mesma máquina que, em outro workflow,
    tem LabInstanceProfile — o quality workflow só pode usar GitHub-hosted."""
    doc = load_yaml(QUALITY)
    for nome_job, job in doc["jobs"].items():
        runs_on = job.get("runs-on")
        assert runs_on == "ubuntu-latest", (
            f"Q2 violada: job '{nome_job}' tem runs-on={runs_on!r}, "
            "esperado exatamente a string 'ubuntu-latest' (nunca self-hosted)."
        )


def test_q3_zero_aws_reference():
    """Q3: o quality workflow roda para PR de qualquer pessoa — qualquer
    referência a AWS (env AWS_*, action aws-actions/*, secrets.AWS_*) é
    superfície de exfiltração de credencial que este arquivo nunca deveria
    ter, mesmo que hoje ele não use nenhuma de fato."""
    raw = load_raw(QUALITY)
    assert not re.search(r"\bAWS_[A-Z_]+\b", raw), (
        "Q3 violada: referência a variável AWS_* encontrada no quality workflow"
    )
    assert "aws-actions/" not in raw, (
        "Q3 violada: uses de aws-actions/* encontrado no quality workflow"
    )
    assert not re.search(r"secrets\.AWS_", raw), (
        "Q3 violada: referência a secrets.AWS_* encontrada no quality workflow"
    )


# --------------------------------------------------------------------------
# Regras D — só 04-2-deploy-v2.yml (tolera ausência do arquivo via skip)
# --------------------------------------------------------------------------


def _load_deploy_or_skip() -> dict:
    _skip_if_missing(DEPLOY)
    return load_yaml(DEPLOY)


def _find_aws_job(doc: dict):
    """Localiza o job que faz o deploy real na AWS.

    Critério primário: `runs-on` contém 'self-hosted' (o único job deste
    workflow que pode ter esse runner, por D3). Se `runs-on` ainda não
    estiver correto (regressão que D3 também vai pegar), cai para uma
    heurística pelo nome do job contendo 'deploy' ou 'aws'.
    """
    for nome_job, job in doc["jobs"].items():
        runs_on = job.get("runs-on")
        candidatos = runs_on if isinstance(runs_on, list) else [runs_on]
        if any(isinstance(c, str) and "self-hosted" in c for c in candidatos):
            return nome_job, job
    for nome_job, job in doc["jobs"].items():
        if re.search(r"deploy|aws", nome_job, re.IGNORECASE):
            return nome_job, job
    pytest.fail(
        "nenhum job de deploy AWS identificável em 04-2-deploy-v2.yml "
        "(nem por runs-on self-hosted, nem por nome contendo deploy/aws)"
    )


def _hard_gate_expression(job: dict):
    if "if" in job:
        return job["if"]
    for step in job.get("steps", []) or []:
        if "if" in step:
            return step["if"]
    return None


def test_d1_only_workflow_dispatch_trigger_ou_skip_se_deploy_ausente():
    """D1: qualquer trigger automático (pull_request/push/schedule) faria
    um job com credencial AWS real disparar sem decisão humana explícita —
    o deploy só pode começar por workflow_dispatch."""
    doc = _load_deploy_or_skip()
    triggers = get_triggers(doc)
    assert set(triggers.keys()) == {"workflow_dispatch"}, (
        "D1 violada: 'on:' do deploy workflow deve conter exatamente "
        f"{{'workflow_dispatch'}}, encontrado: {set(triggers.keys())}"
    )


def test_d2_inputs_are_closed_enum_ou_skip_se_deploy_ausente():
    """D2: um input de release/endpoint livre (string arbitrária) abriria
    caminho para shell/terraform injection via github.event.inputs — o
    enum fechado elimina a superfície antes mesmo do gate D5."""
    doc = _load_deploy_or_skip()
    inputs = get_triggers(doc)["workflow_dispatch"]["inputs"]
    release = inputs["release"]
    assert release.get("type") == "choice", (
        f"D2 violada: input 'release' deve ser type: choice, encontrado: {release.get('type')!r}"
    )
    assert release.get("options") == ["v2"], (
        f"D2 violada: input 'release' deve ter options: [v2], encontrado: {release.get('options')!r}"
    )
    confirm = inputs.get("confirm")
    assert confirm is not None, (
        "D2 violada: input 'confirm' (literal DEPLOY_V2) ausente"
    )
    assert confirm.get("type", "string") == "string", (
        f"D2 violada: input 'confirm' deveria ser string livre validada no gate, "
        f"encontrado type={confirm.get('type')!r}"
    )


def test_d3_aws_job_runs_on_correct_label_ou_skip_se_deploy_ausente():
    """D3: a label composta exata evita que o job AWS caia em qualquer
    runner self-hosted genérico do repositório/organização — só a máquina
    efêmera registrada especificamente para este lab pode pegar o job."""
    doc = _load_deploy_or_skip()
    _, job = _find_aws_job(doc)
    runs_on = job.get("runs-on")
    assert isinstance(runs_on, list), (
        f"D3 violada: runs-on do job AWS deve ser uma lista, encontrado: {runs_on!r}"
    )
    assert set(runs_on) == {"self-hosted", "linux", "x64", "academy-slm-deploy"}, (
        f"D3 violada: runs-on do job AWS deve ser exatamente "
        f"[self-hosted, linux, x64, academy-slm-deploy], encontrado: {runs_on!r}"
    )


def test_d4_aws_job_needs_quality_ou_skip_se_deploy_ausente():
    """D4: sem depender do job de quality, o deploy poderia rodar mesmo
    com lint/teste/scan quebrado — o `needs` é o que torna o gate de
    qualidade obrigatório, não decorativo."""
    doc = _load_deploy_or_skip()
    nome_job, job = _find_aws_job(doc)
    needs = job.get("needs")
    if isinstance(needs, str):
        needs = [needs]
    assert needs, f"D4 violada: job '{nome_job}' não declara 'needs' nenhum"
    outros_jobs = set(doc["jobs"].keys()) - {nome_job}
    assert set(needs) & outros_jobs, (
        f"D4 violada: 'needs' do job '{nome_job}' ({needs!r}) não referencia "
        f"nenhum outro job deste workflow ({outros_jobs!r}) — deploy não pode "
        "rodar isolado do gate de quality/preflight."
    )


def test_d5_hard_gate_present_ou_skip_se_deploy_ausente():
    """D5: se as três condições (owner, branch master, confirmação
    DEPLOY_V2) não estiverem combinadas na mesma expressão booleana, um
    disparo parcialmente válido (ex.: fork com confirm certo) poderia
    acionar o job com credencial AWS real."""
    doc = _load_deploy_or_skip()
    _, job = _find_aws_job(doc)
    expressao = _hard_gate_expression(job)
    assert expressao, (
        "D5 violada: nenhuma condição 'if:' encontrada no job AWS nem em "
        "seus steps — o hard gate não existe."
    )
    assert "github.actor == github.repository_owner" in expressao, (
        f"D5 violada: expressão do gate não compara github.actor com "
        f"github.repository_owner: {expressao!r}"
    )
    assert "github.ref == 'refs/heads/master'" in expressao, (
        f"D5 violada: expressão do gate não fixa github.ref em "
        f"refs/heads/master: {expressao!r}"
    )
    assert "DEPLOY_V2" in expressao, (
        f"D5 violada: expressão do gate não compara o input de confirmação "
        f"com o literal DEPLOY_V2: {expressao!r}"
    )
    assert "&&" in expressao, (
        f"D5 violada: as três condições precisam estar unidas por && na "
        f"mesma expressão booleana, não em ifs desconectados: {expressao!r}"
    )


def test_d6_permissions_contents_read_ou_skip_se_deploy_ausente():
    """D6: idêntica a G4, reforçada aqui porque este é o workflow com
    acesso real à AWS — nenhuma folga extra de permissions é aceitável."""
    doc = _load_deploy_or_skip()
    assert doc.get("permissions") == {"contents": "read"}, (
        f"D6 violada: permissions do deploy workflow deve ser exatamente "
        f"{{'contents': 'read'}}, encontrado: {doc.get('permissions')!r}"
    )


def test_d7_every_job_has_timeout_ou_skip_se_deploy_ausente():
    """D7: sem timeout-minutes, um job AWS travado (ex.: esperando
    InService que nunca chega) consome runner self-hosted indefinidamente
    e pode gerar custo/exposição além do esperado."""
    doc = _load_deploy_or_skip()
    for nome_job, job in doc["jobs"].items():
        timeout = job.get("timeout-minutes")
        assert isinstance(timeout, int) and timeout > 0, (
            f"D7 violada: job '{nome_job}' sem timeout-minutes inteiro "
            f"positivo, encontrado: {timeout!r}"
        )


def test_d8_concurrency_group_is_deterministic_ou_skip_se_deploy_ausente():
    """D8: um group baseado em github.run_id nunca colide — anularia o
    propósito do lock e permitiria dois deploys V2 concorrentes disputando
    o mesmo endpoint self-hosted/AWS."""
    doc = _load_deploy_or_skip()
    concurrency = doc.get("concurrency")
    if not concurrency:
        _, job = _find_aws_job(doc)
        concurrency = job.get("concurrency")
    assert concurrency and concurrency.get("group"), (
        "D8 violada: nenhum 'concurrency.group' encontrado no workflow nem "
        "no job de deploy."
    )
    group = concurrency["group"]
    assert "github.run_id" not in group and "github.run_number" not in group, (
        f"D8 violada: concurrency.group usa run_id/run_number (nunca "
        f"colide, anula o lock): {group!r}"
    )


def test_d9_checkout_persist_credentials_false_and_ref_pinned_ou_skip_se_deploy_ausente():
    """D9: sem persist-credentials:false o token fica no .git/config do
    runner após o job; sem ref: github.sha o checkout pegaria o HEAD atual
    da branch, potencialmente diferente do commit auditado no momento do
    disparo — quebra a garantia de "o que foi aprovado é o que rodou"."""
    doc = _load_deploy_or_skip()
    _, job = _find_aws_job(doc)
    step_checkout = next(
        (
            s
            for s in job.get("steps", []) or []
            if str(s.get("uses", "")).startswith("actions/checkout@")
        ),
        None,
    )
    assert step_checkout is not None, (
        "D9 violada: job de deploy AWS não tem step actions/checkout"
    )
    with_bloco = step_checkout.get("with") or {}
    assert with_bloco.get("persist-credentials") is False, (
        "D9 violada: checkout do job de deploy sem persist-credentials: false "
        f"(literal booleano), encontrado: {with_bloco.get('persist-credentials')!r}"
    )
    ref = with_bloco.get("ref")
    assert ref and "github.sha" in ref, (
        f"D9 violada: checkout do job de deploy sem ref: apontando para "
        f"github.sha (commit exato do dispatch), encontrado: {ref!r}"
    )


def test_d10_no_aws_credential_action_ou_skip_se_deploy_ausente():
    """D10: aws-actions/configure-aws-credentials é o vetor mais direto de
    injetar chave AWS estática no GitHub — proibido mesmo neste workflow,
    que já resolve credencial via IMDSv2/LabInstanceProfile no runner."""
    _load_deploy_or_skip()
    raw = load_raw(DEPLOY)
    assert "aws-actions/" not in raw, (
        "D10 violada: uses de aws-actions/* encontrado no deploy workflow"
    )


def test_d11_no_raw_input_interpolation_in_run_ou_skip_se_deploy_ausente():
    """D11: github.event.inputs.* interpolado direto num run: permite que
    quem dispara o workflow injete comando de shell através do próprio
    input que o D2 tentou restringir a enum — reforço específico de G5."""
    doc = _load_deploy_or_skip()
    padrao = re.compile(r"\$\{\{\s*github\.event\.inputs")
    for nome_job, nome_step, bloco in _iter_run_blocks(doc):
        assert not padrao.search(bloco), (
            f"D11 violada em job '{nome_job}', step '{nome_step}': "
            "github.event.inputs interpolado direto em run: — o valor "
            "precisa passar por env: primeiro."
        )


def test_d12_all_actions_pinned_ou_skip_se_deploy_ausente():
    """D12: reforço de G1 dedicado a este arquivo — é o workflow com
    acesso real à AWS, então uma regressão aqui é a mais cara de todas."""
    _load_deploy_or_skip()
    usos = USES_RE.findall(load_raw(DEPLOY))
    assert usos, "nenhuma linha 'uses:' encontrada no deploy workflow"
    for valor in usos:
        assert SHA40_RE.fullmatch(valor), (
            f"D12 violada: '{valor}' não está pinado por SHA de 40 hex"
        )
