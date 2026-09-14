"""Testes de `packaging.py` (`package`/`validate-package`) e do núcleo de
completude de `evidence.py` (`evaluate_completeness`) que `packaging.py`
usa como uma das precondições.

`/tmp/tf-final/a7/fixtures/` está vazio nesta sessão (A7 ainda não entregou
fixtures prontas) — todo fixture aqui é construído pela própria suíte, com
JSON sintético no schema real confirmado em `/tmp/tf-final/shared/PEDIDOS.md`
(entrada a6b: `alarm.json` aninhado, `baseline-drift.json`/`production-drift.json`
com `data_drift_psi_max`/`prediction_drift_psi`, não os nomes que
`evidence.py` tentava em primeiro lugar — por isso os testes aqui usam
justamente esses nomes alternativos, para provar que `_first()` cobre o caso
real, não só o nome ideal).

Nenhum teste toca em AWS: `_account_id`/`_git_commit` só entram em
`_build_manifest` (chamado por `evidence.cmd_evidence`, fora do escopo direto
de `packaging.py`, que só lê um `manifest.json` já existente).
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from final_project import evidence, packaging

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --------------------------------------------------------------------------- #
# _is_forbidden — cada categoria de exclusão
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "relative",
    [
        "terraform/.terraform/providers/x.tf",
        "src/final_project/__pycache__/config.cpython-312.pyc",
        "terraform/terraform.tfvars",
        "student/credentials",
        "terraform/artifact.auto.tfvars.json",
        "terraform/tfplan.bin",
        "terraform/crash.log",
        "terraform/x.tfstate",
        "terraform/x.tfplan",
        "student/chave.pem",
        "student/chave.ppk",
        "src/final_project/x.pyc",
        "artifacts/model.pkl",
        "artifacts/model.joblib",
        "artifacts/model.tar.gz",
        "algum/.aws/credentials",
    ],
)
def test_is_forbidden_detecta_cada_categoria_de_exclusao(relative):
    assert packaging._is_forbidden(relative) is not None


@pytest.mark.parametrize(
    "relative",
    [
        "src/final_project/config.py",
        "student/solution.yaml",
        "student/DECISION.md",
        "terraform/main.tf",
        "config/acceptance.yaml",
        "diagramas/arquitetura.png",
    ],
)
def test_is_forbidden_nao_marca_arquivo_legitimo(relative):
    assert packaging._is_forbidden(relative) is None


# --------------------------------------------------------------------------- #
# _scan_secrets — cada categoria de segredo, e o que NÃO é segredo
# --------------------------------------------------------------------------- #


def _scan_text(tmp_path: Path, text: str) -> list[str]:
    path = tmp_path / "arquivo.txt"
    path.write_text(text, encoding="utf-8")
    return packaging._scan_secrets(path, "arquivo.txt")


def test_scan_secrets_encontra_access_key_id(tmp_path):
    achados = _scan_text(tmp_path, "aws_access_key_id = AKIAABCDEFGHIJKLMNOP\n")
    assert achados and "access key id" in achados[0]


def test_scan_secrets_encontra_cabecalho_de_chave_privada(tmp_path):
    achados = _scan_text(
        tmp_path,
        "-----BEGIN RSA PRIVATE KEY-----\nMIIB...\n-----END RSA PRIVATE KEY-----\n",
    )
    assert achados and "chave privada" in achados[0]


def test_scan_secrets_encontra_session_token_com_valor_plausivel(tmp_path):
    achados = _scan_text(
        tmp_path, "aws_session_token = FwoGZXIvYXdzEAvJv0aQ9example1234567890\n"
    )
    assert achados and "session token" in achados[0]


def test_scan_secrets_encontra_secret_access_key_com_valor_plausivel(tmp_path):
    achados = _scan_text(
        tmp_path, "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n"
    )
    assert achados and "secret access key" in achados[0]


def test_scan_secrets_nao_marca_account_id_como_segredo(tmp_path):
    """Decisão documentada em `packaging.py`: account id não autentica nada
    por si só, e evidência obrigatória (`cleanup.json`/`manifest.json`)
    precisa registrá-lo em texto claro."""
    achados = _scan_text(
        tmp_path, '{"account": "123456789012", "region": "us-east-1"}\n'
    )
    assert achados == []


def test_scan_secrets_nao_marca_so_o_nome_da_chave_sem_valor_plausivel(tmp_path):
    """`_SESSION_TOKEN_RE`/`_SECRET_KEY_RE` exigem valor de pelo menos 10
    caracteres depois do marcador — do contrário o próprio `packaging.py`
    (que precisa mencionar essas strings para poder procurá-las) sempre daria
    falso positivo em si mesmo."""
    achados = _scan_text(
        tmp_path,
        "mencao solta a aws_session_token e aws_secret_access_key, sem valor de verdade\n",
    )
    assert achados == []


def test_scan_secrets_pula_arquivo_binario_conhecido(tmp_path):
    path = tmp_path / "logo.png"
    path.write_bytes(b"AKIAABCDEFGHIJKLMNOP" + b"\x00\x01\x02")
    assert packaging._scan_secrets(path, "logo.png") == []


# --------------------------------------------------------------------------- #
# _check_cleanup_pass — a recusa estrutural de `package`
# --------------------------------------------------------------------------- #


def test_check_cleanup_pass_falha_quando_arquivo_ausente(minimal_config):
    ok, detail, data = packaging._check_cleanup_pass(minimal_config)
    assert ok is False
    assert "não existe" in detail
    assert data is None


def test_check_cleanup_pass_falha_com_json_invalido(minimal_config):
    path = minimal_config.evidence_dir / "cleanup.json"
    path.write_text("{ não é json", encoding="utf-8")
    ok, detail, _data = packaging._check_cleanup_pass(minimal_config)
    assert ok is False
    assert "não é um JSON válido" in detail


def test_check_cleanup_pass_falha_quando_passed_e_false(minimal_config):
    path = minimal_config.evidence_dir / "cleanup.json"
    path.write_text(json.dumps({"passed": False, "checks_total": 12}), encoding="utf-8")
    ok, detail, data = packaging._check_cleanup_pass(minimal_config)
    assert ok is False
    assert "passed: true" in detail
    assert data == {"passed": False, "checks_total": 12}


def test_check_cleanup_pass_ok_quando_passed_e_true(minimal_config):
    path = minimal_config.evidence_dir / "cleanup.json"
    path.write_text(json.dumps({"passed": True, "checks_total": 12}), encoding="utf-8")
    ok, _detail, data = packaging._check_cleanup_pass(minimal_config)
    assert ok is True
    assert data["passed"] is True


# --------------------------------------------------------------------------- #
# _check_decision — contagem de placeholder
# --------------------------------------------------------------------------- #


def test_check_decision_falha_quando_arquivo_ausente(minimal_config):
    ok, detail = packaging._check_decision(minimal_config)
    assert ok is False
    assert "não encontrado" in detail


def test_check_decision_falha_com_placeholder_pendente(minimal_config):
    path = minimal_config.root / "student" / "DECISION.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Resumo: <preencher>\nOutro campo: <preencher>\n", encoding="utf-8")
    ok, detail = packaging._check_decision(minimal_config)
    assert ok is False
    assert "2 ocorrência" in detail


def test_check_decision_ok_sem_placeholder(minimal_config):
    path = minimal_config.root / "student" / "DECISION.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Resumo: tudo preenchido.\n", encoding="utf-8")
    ok, _detail = packaging._check_decision(minimal_config)
    assert ok is True


# --------------------------------------------------------------------------- #
# evaluate_completeness (evidence.py) — contratos numéricos, schema real
# (chaves alternativas confirmadas em PEDIDOS.md/a6b)
# --------------------------------------------------------------------------- #

GOOD_ACCEPTANCE = {
    "workload_counts": {"atendimento_output_count": 40, "campanha_output_count": 600},
    "drift": {
        "baseline": {"data_psi_max_below": 0.10, "prediction_psi_below": 0.10},
        "shifted": {
            "data_psi_max_at_least": 0.30,
            "features_above_threshold_at_least": 3,
            "prediction_psi_at_least": 0.20,
        },
    },
}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _populate_good_evidence(evidence_dir: Path) -> None:
    """Preenche `evidence_dir` com os 15 arquivos de `evidence.
    REQUIRED_EVIDENCE_FILES`, todos batendo com os quatro contratos numéricos
    de `GOOD_ACCEPTANCE`. `baseline-drift.json`/`production-drift.json` usam
    de propósito os nomes de chave reais (`data_drift_psi_max`/
    `prediction_drift_psi`), não os nomes "ideais" que `evidence.py` tenta
    primeiro — é exatamente o caso que `_first()` existe para cobrir."""
    for name in (
        "data-contract.json",
        "training.json",
        "artifact.json",
        "serving.json",
        "alarm.json",
        "reaction.json",
        "dashboard.json",
    ):
        _write_json(evidence_dir / name, {"placeholder": True})
    (evidence_dir / "candidates.md").write_text("# candidatos\n", encoding="utf-8")
    _write_json(evidence_dir / "candidates.json", {"candidates": []})
    _write_json(
        evidence_dir / "solution.json",
        {"atendimento_pattern": "realtime", "campanha_pattern": "async"},
    )
    _write_json(evidence_dir / "atendimento.json", {"request_count": 40})
    _write_json(evidence_dir / "campanha.json", {"output_count": 600})
    _write_json(
        evidence_dir / "baseline-drift.json",
        {"data_drift_psi_max": 0.05, "prediction_drift_psi": 0.02},
    )
    _write_json(
        evidence_dir / "production-drift.json",
        {
            "data_drift_psi_max": 0.60,
            "prediction_drift_psi": 0.25,
            "features_above_threshold": [
                "support_calls_90d",
                "monthly_charges",
                "payment_delay_days",
            ],
        },
    )
    _write_json(
        evidence_dir / "quality.json", {"windows": {"baseline": {}, "shifted": {}}}
    )


@pytest.fixture
def config_com_evidencia_completa(minimal_config):
    _populate_good_evidence(minimal_config.evidence_dir)
    return replace(minimal_config, acceptance=GOOD_ACCEPTANCE)


def test_evaluate_completeness_passa_com_evidencia_e_contratos_batendo(
    config_com_evidencia_completa,
):
    checks, parsed = evidence.evaluate_completeness(config_com_evidencia_completa)
    assert not checks.failed, checks.failed
    assert "production-drift.json" in parsed


def test_evaluate_completeness_falha_quando_falta_um_arquivo(
    config_com_evidencia_completa,
):
    (config_com_evidencia_completa.evidence_dir / "dashboard.json").unlink()
    checks, _ = evidence.evaluate_completeness(config_com_evidencia_completa)
    nomes_reprovados = {item["check"] for item in checks.failed}
    assert "arquivo_presente:dashboard.json" in nomes_reprovados


def test_evaluate_completeness_falha_quando_contagem_de_atendimento_diverge(
    config_com_evidencia_completa,
):
    _write_json(
        config_com_evidencia_completa.evidence_dir / "atendimento.json",
        {"request_count": 39},
    )
    checks, _ = evidence.evaluate_completeness(config_com_evidencia_completa)
    nomes_reprovados = {item["check"] for item in checks.failed}
    assert "contrato_atendimento" in nomes_reprovados


def test_evaluate_completeness_falha_quando_psi_baseline_nao_fica_abaixo_do_limiar(
    config_com_evidencia_completa,
):
    _write_json(
        config_com_evidencia_completa.evidence_dir / "baseline-drift.json",
        {"data_drift_psi_max": 0.35, "prediction_drift_psi": 0.02},
    )
    checks, _ = evidence.evaluate_completeness(config_com_evidencia_completa)
    nomes_reprovados = {item["check"] for item in checks.failed}
    assert "contrato_psi_baseline" in nomes_reprovados


def test_evaluate_completeness_falha_quando_shifted_nao_cruza_features_suficientes(
    config_com_evidencia_completa,
):
    _write_json(
        config_com_evidencia_completa.evidence_dir / "production-drift.json",
        {
            "data_drift_psi_max": 0.60,
            "prediction_drift_psi": 0.25,
            "features_above_threshold": [
                "support_calls_90d"
            ],  # só 1, contrato exige >= 3
        },
    )
    checks, _ = evidence.evaluate_completeness(config_com_evidencia_completa)
    nomes_reprovados = {item["check"] for item in checks.failed}
    assert "contrato_psi_shifted" in nomes_reprovados


# --------------------------------------------------------------------------- #
# cmd_package / cmd_validate_package — precondições isoladas
# --------------------------------------------------------------------------- #


def test_cmd_package_recusa_sem_cleanup_json(minimal_config, capsys):
    exit_code = packaging.cmd_package(minimal_config, None)
    assert exit_code == 1


def test_cmd_package_recusa_com_cleanup_passed_false(minimal_config):
    _write_json(minimal_config.evidence_dir / "cleanup.json", {"passed": False})
    exit_code = packaging.cmd_package(minimal_config, None)
    assert exit_code == 1


# --------------------------------------------------------------------------- #
# cmd_package + cmd_validate_package — caminho feliz de ponta a ponta
# --------------------------------------------------------------------------- #


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def projeto_empacotavel(config_com_evidencia_completa):
    """Monta, em `tmp_path`, o mínimo que `cmd_package` e `cmd_validate_package`
    exigem: os arquivos que `_collect_entries` copia + tudo que a checagem de
    estrutura de `cmd_validate_package` exige dentro do zip. Nenhum conteúdo
    de `src/scripts/lambda/terraform` é código real — só marcadores de
    presença, porque `_collect_entries`/`_walk_tree` não interpretam
    conteúdo."""
    cfg = config_com_evidencia_completa
    root = cfg.root

    _write_text(root / "Makefile", "bootstrap:\n\t@echo ok\n")
    _write_text(root / "requirements.txt", "pyyaml\n")
    _write_text(root / "config" / "scenario.yaml", "dataset:\n  feature_order: []\n")
    _write_text(root / "config" / "acceptance.yaml", "workload_counts: {}\n")
    _write_text(
        root / "student" / "solution.yaml",
        (
            "version: 1\n"
            "serving:\n"
            "  atendimento:\n"
            "    pattern: realtime\n"
            "  campanha:\n"
            "    pattern: async\n"
            "authors:\n"
            '  group: "Grupo Teste"\n'
            "  members:\n"
            '    - "Fulano da Silva (RM123456)"\n'
        ),
    )
    _write_text(
        root / "student" / "DECISION.md", "Decisão registrada, sem placeholder.\n"
    )
    _write_text(root / "src" / "final_project" / "marker.py", "# marcador\n")
    _write_text(root / "scripts" / "marker.py", "# marcador\n")
    _write_text(root / "lambda" / "marker.py", "# marcador\n")
    _write_text(root / "terraform" / "marker.tf", "# marcador\n")
    (root / "diagramas").mkdir(parents=True, exist_ok=True)
    (root / "diagramas" / "arquitetura.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    )

    _write_json(
        cfg.evidence_dir / "cleanup.json",
        {"passed": True, "checks_total": 12, "checks_failed": 0},
    )
    _write_json(
        cfg.evidence_dir / "manifest.json",
        {
            "schema_version": "1.0.0",
            "git_commit": "0000000",
            "account": "123456789012",
            "region": cfg.region,
            "prefix": cfg.prefix,
            "patterns": {"atendimento": "realtime", "campanha": "async"},
            "files": {},
        },
    )
    _write_text(
        cfg.evidence_dir / "evidence.md", "# Dossiê\n\nSem recomendação de pattern.\n"
    )

    return cfg


def test_cmd_package_gera_zip_e_cmd_validate_package_aprova(
    projeto_empacotavel, capsys
):
    exit_code_package = packaging.cmd_package(projeto_empacotavel, None)
    saida_package = capsys.readouterr()
    assert exit_code_package == 0, saida_package.err

    zip_path = projeto_empacotavel.root / packaging.ZIP_FILENAME
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        nomes = set(zf.namelist())
    assert f"{packaging.ZIP_ROOT_NAME}/SUBMISSION.md" in nomes
    assert f"{packaging.ZIP_ROOT_NAME}/student/solution.yaml" in nomes
    assert f"{packaging.ZIP_ROOT_NAME}/evidence/quality.json" in nomes
    # nada de .terraform/.venv/credencial pode ter entrado, mesmo sem existir
    # no fixture: prova que a seleção é por whitelist, não por filtro de exclusão.
    assert not any(".terraform" in nome or ".venv" in nome for nome in nomes)

    exit_code_validate = packaging.cmd_validate_package(projeto_empacotavel, None)
    saida_validate = capsys.readouterr()
    assert exit_code_validate == 0, saida_validate.out
    assert "[PASS] pacote pronto para o portal FIAP" in saida_validate.out
    assert "[FAIL]" not in saida_validate.out


def test_cmd_validate_package_reprova_quando_decision_tem_placeholder_apos_o_package(
    projeto_empacotavel, capsys
):
    """`package` já rodou com DECISION.md limpo; um placeholder introduzido
    DEPOIS (ex.: alguém reabriu e mexeu no arquivo sem gerar o zip de novo)
    só é pego se o pacote for reconstruído — este teste prova o caminho
    inverso: corrompendo o CONTEÚDO do zip diretamente, sem tocar no
    repositório, `validate-package` ainda reprova porque relê o zip, não o
    disco."""
    packaging.cmd_package(projeto_empacotavel, None)
    capsys.readouterr()

    zip_path = projeto_empacotavel.root / packaging.ZIP_FILENAME
    _corromper_decision_dentro_do_zip(zip_path)

    exit_code = packaging.cmd_validate_package(projeto_empacotavel, None)
    saida = capsys.readouterr()
    assert exit_code == 1
    assert "[FAIL] decisão" in saida.out
    assert "[FAIL] pacote não está pronto para o portal FIAP" in saida.out


def _corromper_decision_dentro_do_zip(zip_path: Path) -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = Path(tmp) / "extract"
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
        decision_path = (
            extract_dir / packaging.ZIP_ROOT_NAME / "student" / "DECISION.md"
        )
        decision_path.write_text("Ainda falta: <preencher>\n", encoding="utf-8")
        zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(extract_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, str(path.relative_to(extract_dir)))


# --------------------------------------------------------------------------- #
# Os cinco caminhos de recusa exigidos pelo enunciado, verificados de ponta a
# ponta via `cmd_package` (não só na função de checagem isolada acima) —
# cada um parte do MESMO projeto empacotável e quebra exatamente uma coisa,
# provando que o zip nem chega a nascer.
# --------------------------------------------------------------------------- #


def test_cmd_package_recusa_quando_solution_yaml_ainda_tem_todo(
    projeto_empacotavel, capsys
):
    sol_path = projeto_empacotavel.root / "student" / "solution.yaml"
    sol_path.write_text(
        "version: 1\n"
        "serving:\n"
        "  atendimento:\n"
        "    pattern: TODO\n"
        "  campanha:\n"
        "    pattern: async\n"
        "authors:\n"
        '  group: "Grupo Teste"\n'
        "  members:\n"
        '    - "Fulano da Silva (RM123456)"\n',
        encoding="utf-8",
    )

    exit_code = packaging.cmd_package(projeto_empacotavel, None)
    saida = capsys.readouterr()

    assert exit_code == 1
    assert "TODO" in saida.err
    assert not (projeto_empacotavel.root / packaging.ZIP_FILENAME).exists()


def test_cmd_package_recusa_quando_decision_md_tem_placeholder(
    projeto_empacotavel, capsys
):
    decision_path = projeto_empacotavel.root / "student" / "DECISION.md"
    decision_path.write_text("Resumo: <preencher>\n", encoding="utf-8")

    exit_code = packaging.cmd_package(projeto_empacotavel, None)
    saida = capsys.readouterr()

    assert exit_code == 1
    assert "preencher" in saida.err
    assert not (projeto_empacotavel.root / packaging.ZIP_FILENAME).exists()


@pytest.mark.parametrize(
    "relative_proibido",
    [
        "terraform/estado.tfstate",
        "src/final_project/modelo.pkl",
        "lambda/chave.pem",
    ],
)
def test_cmd_package_recusa_com_arquivo_proibido_dentro_de_subarvore_incluida(
    projeto_empacotavel, capsys, relative_proibido
):
    """`tfstate`, `.pem` e extensão de binário de modelo pousados DENTRO de
    uma subárvore que `_collect_entries` de fato varre
    (`src/scripts/lambda/terraform`) — é o caso real de erro: alguém deixou
    um arquivo indevido dentro de um diretório incluído, e a rede de
    segurança (`_check_forbidden`) precisa pegar antes do zip nascer."""
    destino = projeto_empacotavel.root / relative_proibido
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("conteudo indevido\n", encoding="utf-8")

    exit_code = packaging.cmd_package(projeto_empacotavel, None)
    saida = capsys.readouterr()

    assert exit_code == 1
    assert "proibido" in saida.err
    assert not (projeto_empacotavel.root / packaging.ZIP_FILENAME).exists()


@pytest.mark.parametrize(
    "relative_fora_da_selecao",
    [
        "artifacts/data/reference.csv",
        ".generated/segredo.json",
        "terraform/.terraform/providers/marker",
    ],
)
def test_cmd_package_nunca_inclui_artifacts_data_nem_generated_mesmo_se_existirem(
    projeto_empacotavel, capsys, relative_fora_da_selecao
):
    """`artifacts/data/`, `.generated/` e qualquer coisa dentro de
    `.terraform/` não aparecem no zip por uma proteção estrutural, não pela
    lista de proibidos: `_walk_tree` nem desce nesses diretórios (estão em
    `_EXCLUDE_DIR_NAMES` ou simplesmente fora das quatro subárvores que
    `_collect_entries` varre). Plantar um arquivo ali não deve fazer
    `package` recusar por engano nem, mais importante, fazer esse arquivo
    aparecer no zip."""
    destino = projeto_empacotavel.root / relative_fora_da_selecao
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("nao deveria viajar no pacote\n", encoding="utf-8")

    exit_code = packaging.cmd_package(projeto_empacotavel, None)
    capsys.readouterr()
    assert exit_code == 0

    zip_path = projeto_empacotavel.root / packaging.ZIP_FILENAME
    with zipfile.ZipFile(zip_path) as zf:
        nomes = zf.namelist()
    # confere o CAMINHO plantado inteiro, não só o nome do arquivo — "marker"
    # sozinho também é o nome de arquivos legítimos que a fixture inclui de
    # propósito (terraform/marker.tf etc.), então bastaria o nome para dar
    # falso positivo de vazamento.
    assert not any(relative_fora_da_selecao in nome for nome in nomes)


def test_cmd_package_recusa_com_credencial_plantada_sem_sanitizar(
    projeto_empacotavel, capsys
):
    """A recusa precisa ser em voz alta (`exit_code == 1`, sem zip) — nunca uma
    limpeza silenciosa do arquivo com o segredo dentro."""
    marker_path = projeto_empacotavel.root / "src" / "final_project" / "marker.py"
    conteudo_original = marker_path.read_text(encoding="utf-8")
    marker_path.write_text(
        conteudo_original
        + '\naws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n',
        encoding="utf-8",
    )

    exit_code = packaging.cmd_package(projeto_empacotavel, None)
    saida = capsys.readouterr()

    assert exit_code == 1
    assert "segredo" in saida.err
    assert not (projeto_empacotavel.root / packaging.ZIP_FILENAME).exists()
    # a mensagem denuncia a categoria, nunca o valor casado do segredo.
    assert "wJalrXUtnFEMI" not in saida.err


def test_zip_do_caminho_feliz_nao_contem_nenhum_item_proibido(
    projeto_empacotavel, capsys
):
    """Prova exigida separadamente do caminho feliz: reabre o zip já gerado e
    testa CADA membro contra `_is_forbidden`, não só alguns nomes pontuais —
    o pacote nasce por whitelist, então isto deveria dar zero ocorrências por
    construção."""
    exit_code = packaging.cmd_package(projeto_empacotavel, None)
    capsys.readouterr()
    assert exit_code == 0

    zip_path = projeto_empacotavel.root / packaging.ZIP_FILENAME
    with zipfile.ZipFile(zip_path) as zf:
        nomes = zf.namelist()

    prefixo = f"{packaging.ZIP_ROOT_NAME}/"
    achados = []
    for nome in nomes:
        relativo = nome.removeprefix(prefixo)
        if relativo in ("", "SUBMISSION.md"):
            continue
        motivo = packaging._is_forbidden(relativo)
        if motivo:
            achados.append((relativo, motivo))

    assert achados == []
