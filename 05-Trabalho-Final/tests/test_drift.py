"""Testes de PSI (`drift.py`) e de qualidade com ground truth (`evaluation.py`).

Mesma separação de papel dos dois módulos: `drift.py` compara distribuição
(nunca olha rótulo), `evaluation.py` compara predição com rótulo verdadeiro.
O PSI é o número que decide se o alarme dispara — um erro aqui não aparece
como excecão, aparece como um alarme que nunca toca ou que toca sempre.
"""

from __future__ import annotations

import csv
import math

import numpy as np
import pytest

from final_project import drift, evaluation

BINS = 5
EPSILON = 1e-6


# --------------------------------------------------------------------------- #
# PSI — matemática pura
# --------------------------------------------------------------------------- #


def test_distribuicoes_identicas_dao_psi_proximo_de_zero():
    amostra = np.linspace(0.0, 100.0, 600)
    valor, _, _, binning = drift.psi(amostra, amostra, BINS, EPSILON)
    assert binning == "quantil"
    assert valor == pytest.approx(0.0, abs=1e-9)


def test_deslocamento_forte_produz_psi_acima_de_0_20():
    rng = np.random.default_rng(20260913)
    referencia = rng.normal(100.0, 15.0, 1000)
    deslocada = rng.normal(180.0, 15.0, 1000)
    valor, _, _, _ = drift.psi(referencia, deslocada, BINS, EPSILON)
    assert valor > 0.20


def test_psi_e_deterministico_para_o_mesmo_par_de_amostras():
    """Chamar duas vezes com o mesmo par tem que devolver exatamente o mesmo
    número — nenhuma aleatoriedade escondida (ex.: ordem de iteração de set)
    pode entrar na conta do alarme."""
    referencia = np.linspace(0.0, 50.0, 400)
    observada = np.linspace(10.0, 60.0, 400)

    primeira, primeira_ref, primeira_obs, _ = drift.psi(
        referencia, observada, BINS, EPSILON
    )
    segunda, segunda_ref, segunda_obs, _ = drift.psi(
        referencia, observada, BINS, EPSILON
    )

    assert primeira == segunda
    assert not any(math.isnan(x) for x in primeira_ref)
    assert not math.isnan(primeira)
    assert primeira_ref == segunda_ref
    assert primeira_obs == segunda_obs


def test_bin_vazio_nao_gera_divisao_por_zero_nem_infinito():
    """Bug clássico de PSI: um bin sem nenhuma observação dá proporção zero,
    e log(0) ou divisão por zero explodiria o número. `epsilon` no lugar da
    proporção zero é o que evita isso — testado aqui explicitamente."""
    referencia = np.linspace(0.0, 100.0, 1000)
    observada = np.full(300, 99.9)  # toda a massa observada cai num só bin

    valor, ref_share, obs_share, _ = drift.psi(referencia, observada, BINS, EPSILON)

    assert np.isfinite(valor)
    assert not any(math.isinf(x) or math.isnan(x) for x in obs_share)
    assert not any(math.isinf(x) or math.isnan(x) for x in ref_share)
    assert min(obs_share) == pytest.approx(EPSILON)


def test_bin_vazio_no_caminho_categorico_tambem_nao_gera_infinito():
    """Mesmo bug, caminho categórico: uma categoria que só existe de um lado."""
    referencia = np.array([0.0] * 70 + [1.0] * 30)
    observada = np.array([0.0] * 100)  # categoria 1.0 nunca aparece na janela observada

    valor, _ref_share, obs_share, binning = drift.psi(
        referencia, observada, BINS, EPSILON
    )

    assert binning == "categoria"
    assert np.isfinite(valor)
    assert min(obs_share) == pytest.approx(EPSILON)


def test_referencia_vazia_falha_com_mensagem_clara():
    with pytest.raises(ValueError, match="referencia esta vazia"):
        drift.psi(np.array([]), np.array([1.0, 2.0]), BINS, EPSILON)


def test_janela_observada_vazia_falha_com_mensagem_clara():
    with pytest.raises(ValueError, match="janela observada esta vazia"):
        drift.psi(np.linspace(0, 10, 100), np.array([]), BINS, EPSILON)


# --------------------------------------------------------------------------- #
# Reprodução da calibração — CSVs reais de artifacts/data/
# --------------------------------------------------------------------------- #


def _read_feature_columns(csv_path, feature_order):
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        name: np.array([float(row[name]) for row in rows], dtype=float)
        for name in feature_order
    }


def test_reproducao_calibracao_janela_baseline_fica_abaixo_do_limiar(real_config):
    """`/tmp/tf-final/a2/calibracao.md`, secão "Verificação final": contra os
    CSVs reais, `max_psi_base=0.0598` — bem abaixo do limiar de 0.10 de
    `config/acceptance.yaml` (`drift.baseline.data_psi_max_below`). Só a
    parte de PSI de DADO é reproduzida aqui: `PredictionDriftPSI` depende de
    pontuar o endpoint SageMaker real (rede/AWS), fora do escopo determinístico
    deste teste."""
    bins = real_config.acceptance["drift"]["bins"]
    epsilon = real_config.acceptance["drift"]["epsilon"]
    limiar_baseline = real_config.acceptance["drift"]["baseline"]["data_psi_max_below"]

    referencia = _read_feature_columns(
        real_config.data_dir / "reference.csv", real_config.feature_order
    )
    baseline = _read_feature_columns(
        real_config.data_dir / "production_baseline.csv", real_config.feature_order
    )

    drifts = drift.feature_drift(
        referencia, baseline, real_config.feature_order, bins, epsilon
    )
    max_psi = max(d.psi for d in drifts)

    assert max_psi < limiar_baseline
    # número medido em calibracao.md ("Verificação final"), com folga generosa
    # para arredondamento de `_round_features` na geração real do CSV.
    assert max_psi == pytest.approx(0.0598, abs=0.01)


def test_reproducao_calibracao_janela_shifted_cruza_o_limiar_em_pelo_menos_tres_features(
    real_config,
):
    """`/tmp/tf-final/a2/calibracao.md`: 5 das 7 features cruzam PSI>=0.20 na
    janela shifted (limiar mínimo do contrato é 3, `drift.shifted.
    features_above_threshold_at_least`). `support_calls_90d` é a de maior
    PSI (satura o bin), como no relatório de calibração."""
    bins = real_config.acceptance["drift"]["bins"]
    epsilon = real_config.acceptance["drift"]["epsilon"]
    limiar_feature = real_config.acceptance["drift"]["feature_alert_threshold"]
    minimo_features = real_config.acceptance["drift"]["shifted"][
        "features_above_threshold_at_least"
    ]
    minimo_data_psi = real_config.acceptance["drift"]["shifted"][
        "data_psi_max_at_least"
    ]

    referencia = _read_feature_columns(
        real_config.data_dir / "reference.csv", real_config.feature_order
    )
    shifted = _read_feature_columns(
        real_config.data_dir / "production_shifted.csv", real_config.feature_order
    )

    drifts = drift.feature_drift(
        referencia, shifted, real_config.feature_order, bins, epsilon
    )
    por_feature = {d.feature: d.psi for d in drifts}
    acima_do_limiar = [
        nome for nome, valor in por_feature.items() if valor >= limiar_feature
    ]

    assert len(acima_do_limiar) >= minimo_features
    assert max(por_feature.values()) >= minimo_data_psi

    # números da tabela "Verificação final" de calibracao.md — tolerância
    # generosa (10%) porque comparam duas execuções independentes do gerador
    # com a mesma seed, e o relatório já registra pequenas diferenças de
    # arredondamento entre calibração em memória e CSV gerado em disco.
    assert por_feature["support_calls_90d"] == pytest.approx(2.0524, rel=0.10)
    assert por_feature["monthly_charges"] == pytest.approx(1.1981, rel=0.10)
    assert por_feature["tenure_months"] < limiar_feature  # não deveria acender


def test_interpretacao_segue_as_faixas_de_acceptance_yaml(real_config):
    limiar_feature = real_config.acceptance["drift"]["feature_alert_threshold"]
    assert drift.interpret(0.0) == "estavel"
    assert drift.interpret(limiar_feature) != "estavel"


# --------------------------------------------------------------------------- #
# evaluation.py — F1 / ROC-AUC / matriz de confusão com rótulos conhecidos
# --------------------------------------------------------------------------- #


def test_evaluate_calcula_f1_rocauc_e_matriz_de_confusao_com_rotulos_conhecidos():
    scores_por_id = {"a": 0.9, "b": 0.8, "c": 0.3, "d": 0.1, "e": 0.6}
    rotulos_por_id = {"a": 1, "b": 0, "c": 0, "d": 0, "e": 1}

    relatorio = evaluation.evaluate(
        "baseline", scores_por_id, rotulos_por_id, threshold=0.5
    )

    # threshold 0.5: previsto = [a:1, b:1, c:0, d:0, e:1]; verdade = [a:1,b:0,c:0,d:0,e:1]
    # -> b é falso positivo; nenhum falso negativo.
    assert relatorio.rows == 5
    assert relatorio.true_negatives == 2  # c, d
    assert relatorio.false_positives == 1  # b
    assert relatorio.false_negatives == 0
    assert relatorio.true_positives == 2  # a, e
    assert relatorio.f1 == pytest.approx(0.8)
    assert relatorio.roc_auc == pytest.approx(0.8333333333, abs=1e-6)


def test_evaluate_falha_com_mensagem_quando_falta_predicao_para_ground_truth():
    scores_por_id = {"a": 0.9}
    rotulos_por_id = {"a": 1, "b": 0}
    with pytest.raises(ValueError, match="observation_id"):
        evaluation.evaluate("baseline", scores_por_id, rotulos_por_id, threshold=0.5)


def test_evaluate_caso_degenerado_uma_unica_classe_nao_levanta_excecao():
    """Janela pequena onde ninguém cancelou: só existe rótulo 0. ROC-AUC não é
    definido matematicamente para uma única classe — o sklearn não levanta
    excecão, devolve NaN com aviso (UndefinedMetricWarning). O contrato deste
    teste é: `evaluate()` não pode propagar excecão nesse caso (documentado
    como comportamento atual; ver `/tmp/tf-final/a12/defeitos.md` para a
    consequência de serializar esse NaN em `quality.json`)."""
    scores_por_id = {"a": 0.1, "b": 0.2, "c": 0.3, "d": 0.4}
    rotulos_por_id = {"a": 0, "b": 0, "c": 0, "d": 0}

    relatorio = evaluation.evaluate(
        "shifted", scores_por_id, rotulos_por_id, threshold=0.5
    )

    assert relatorio.f1 == pytest.approx(0.0)
    assert math.isnan(relatorio.roc_auc)
    assert relatorio.true_negatives == 4
    assert relatorio.false_positives == 0
    assert relatorio.false_negatives == 0
    assert relatorio.true_positives == 0


def test_failure_direction_nomeia_o_modo_de_falha_dominante():
    relatorio_fp = evaluation.QualityReport(
        window="shifted",
        rows=200,
        threshold=0.5,
        f1=0.5,
        roc_auc=0.7,
        true_negatives=20,
        false_positives=106,
        false_negatives=1,
        true_positives=72,
        predicted_churn_rate=0.89,
        actual_churn_rate=0.365,
    )
    assert evaluation.failure_direction(relatorio_fp) == "falso_positivo"
