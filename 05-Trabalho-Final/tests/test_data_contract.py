"""Contrato de dados do Trabalho Final, testado contra os 9 CSV reais em
`artifacts/data/` (nunca uma cópia sintética) — se o gerador de outro agente
sair do combinado aqui, este teste denuncia antes do `make validate-data` do
aluno.

Os testes de corrupção usam `copied_data_config` (cópia em `tmp_path`): nunca
escrevemos nos CSV reais do repositório.
"""

from __future__ import annotations

import csv
import math

import pytest

from final_project.data_contract import cmd_validate_data, validate

EXPECTED_ROW_COUNTS = {
    "train.csv": 2800,
    "validation.csv": 600,
    "reference.csv": 600,
    "production_baseline.csv": 200,
    "production_shifted.csv": 200,
    "campaign_features.csv": 600,
    "ground_truth_baseline.csv": 200,
    "ground_truth_shifted.csv": 200,
    "atendimento.csv": 40,
}

# Arquivos sem cabeçalho: canal headerless do XGBoost nativo (rótulo na
# coluna 0, sem id). Todo o resto carrega cabeçalho com `observation_id`.
HEADERLESS_FILES = {"train.csv", "validation.csv"}

FEATURE_ORDER = (
    "tenure_months",
    "monthly_charges",
    "support_calls_90d",
    "payment_delay_days",
    "usage_score",
    "annual_contract",
    "premium_plan",
)

# Payload puro de inferência: id + features, nunca rótulo.
INFERENCE_FILES = (
    "production_baseline.csv",
    "production_shifted.csv",
    "campaign_features.csv",
    "atendimento.csv",
)

ID_BEARING_FILES = (
    "reference.csv",
    *INFERENCE_FILES,
    "ground_truth_baseline.csv",
    "ground_truth_shifted.csv",
)

GROUND_TRUTH_PAIRS = (
    ("production_baseline.csv", "ground_truth_baseline.csv"),
    ("production_shifted.csv", "ground_truth_shifted.csv"),
)


def _read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.reader(handle) if row]


def _data_rows(data_dir, filename):
    """Linhas de dados, sem cabeçalho, independente do arquivo carregar
    cabeçalho ou não — oráculo simples, deliberadamente separado da lógica
    de `data_contract.py` que este teste está verificando."""
    rows = _read_csv(data_dir / filename)
    return rows if filename in HEADERLESS_FILES else rows[1:]


def _header(data_dir, filename):
    rows = _read_csv(data_dir / filename)
    return rows[0]


# --------------------------------------------------------------------------- #
# Contagens exatas
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("filename,expected", list(EXPECTED_ROW_COUNTS.items()))
def test_contagem_exata_de_linhas_por_arquivo(real_config, filename, expected):
    assert len(_data_rows(real_config.data_dir, filename)) == expected


# --------------------------------------------------------------------------- #
# Ordem das features
# --------------------------------------------------------------------------- #


def test_ordem_das_features_no_contrato_de_configuracao(real_config):
    assert real_config.feature_order == list(FEATURE_ORDER)


@pytest.mark.parametrize("filename", ["reference.csv", *INFERENCE_FILES])
def test_cabecalho_dos_arquivos_com_id_segue_a_ordem_das_features(
    real_config, filename
):
    header = _header(real_config.data_dir, filename)
    features_no_cabecalho = header[1 : 1 + len(FEATURE_ORDER)]
    assert features_no_cabecalho == list(FEATURE_ORDER)


# --------------------------------------------------------------------------- #
# Ausência de NaN e Inf
# --------------------------------------------------------------------------- #


def _numeric_column_indices(filename: str) -> list[int]:
    """Índices (na linha de dados, sem cabeçalho) que precisam converter
    para float finito. `observation_id` nunca entra: é string por
    contrato, não uma feature."""
    n_features = len(FEATURE_ORDER)
    if filename in HEADERLESS_FILES:
        return list(range(n_features + 1))  # rótulo + features
    if filename == "reference.csv":
        return list(range(1, 1 + n_features)) + [1 + n_features]  # features + rótulo
    if filename in INFERENCE_FILES:
        return list(range(1, 1 + n_features))  # só features
    return [1]  # ground_truth_*.csv: só o rótulo


@pytest.mark.parametrize("filename", list(EXPECTED_ROW_COUNTS))
def test_sem_nan_ou_inf_nas_colunas_numericas(real_config, filename):
    columns = _numeric_column_indices(filename)
    for row in _data_rows(real_config.data_dir, filename):
        for col in columns:
            assert math.isfinite(float(row[col])), (
                f"{filename}: valor não finito em {row}"
            )


# --------------------------------------------------------------------------- #
# observation_id único e churn binário
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("filename", list(ID_BEARING_FILES))
def test_observation_id_e_unico_por_arquivo(real_config, filename):
    ids = [row[0] for row in _data_rows(real_config.data_dir, filename)]
    assert len(ids) == len(set(ids))


def test_churn_so_assume_0_ou_1(real_config):
    label_sources = {
        "train.csv": lambda row: row[0],
        "validation.csv": lambda row: row[0],
        "reference.csv": lambda row: row[-1],
        "ground_truth_baseline.csv": lambda row: row[-1],
        "ground_truth_shifted.csv": lambda row: row[-1],
    }
    for filename, extract in label_sources.items():
        valores = {extract(row) for row in _data_rows(real_config.data_dir, filename)}
        assert valores <= {"0", "1"}, f"{filename}: valores de rótulo {valores}"


# --------------------------------------------------------------------------- #
# Payload de inferência não carrega target nem id como feature
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("filename", list(INFERENCE_FILES))
def test_payload_de_inferencia_nao_contem_target_nem_id_como_feature(
    real_config, filename
):
    """Contrato de dados explícito: o payload que vai para
    atendimento/campanha nunca pode carregar `churn`. A primeira coluna é o
    id (string de rastreio), nunca contada como feature."""
    header = _header(real_config.data_dir, filename)

    assert real_config.label_column not in header, (
        f"{filename}: rótulo vazou para o payload de inferência"
    )
    assert header[0] == real_config.id_column
    assert header[1:] == list(FEATURE_ORDER), (
        f"{filename}: colunas além do id divergem das features"
    )
    assert len(header) == len(FEATURE_ORDER) + 1, (
        f"{filename}: coluna extra além de id + features"
    )


# --------------------------------------------------------------------------- #
# Cobertura do ground truth
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("producao,ground_truth", list(GROUND_TRUTH_PAIRS))
def test_todo_observation_id_de_producao_tem_rotulo_no_ground_truth(
    real_config, producao, ground_truth
):
    ids_producao = {row[0] for row in _data_rows(real_config.data_dir, producao)}
    ids_ground_truth = {
        row[0] for row in _data_rows(real_config.data_dir, ground_truth)
    }
    assert ids_producao == ids_ground_truth, (
        f"faltando: {ids_producao - ids_ground_truth}; sobrando: {ids_ground_truth - ids_producao}"
    )


# --------------------------------------------------------------------------- #
# Contrato roda de ponta a ponta e falha com exit code != 0 quando corrompido
# --------------------------------------------------------------------------- #


def test_contrato_passa_de_ponta_a_ponta_contra_os_dados_reais(copied_data_config):
    """Sanity check da própria cópia: antes de corromper nada, a cópia
    intacta em tmp_path precisa reportar o mesmo resultado que os dados
    reais — senão os testes de corrupção abaixo não provam nada."""
    report = validate(copied_data_config)
    assert report.ok, [c.as_dict() for c in report.failed]


def test_contrato_falha_com_exit_code_diferente_de_zero_quando_csv_corrompido(
    copied_data_config,
):
    corrupted_path = copied_data_config.data_dir / "atendimento.csv"
    linhas = corrupted_path.read_text(encoding="utf-8").splitlines()
    # Duplica a última linha: quebra a contagem exata (40 -> 41) e o hash do
    # manifesto — duas verificações reprovando ao mesmo tempo, de propósito.
    corrupted_path.write_text("\n".join([*linhas, linhas[-1]]) + "\n", encoding="utf-8")

    report = validate(copied_data_config)
    assert not report.ok
    assert any(
        not check.passed
        for check in report.checks
        if check.name.startswith("row_count")
    )

    exit_code = cmd_validate_data(copied_data_config, None)
    assert exit_code != 0
