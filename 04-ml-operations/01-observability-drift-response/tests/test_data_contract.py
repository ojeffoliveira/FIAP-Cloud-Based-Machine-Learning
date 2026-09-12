"""Contrato do dataset — o que o `make validate-data` cobra, cobrado também aqui.

A diferença de propósito: o `validate-data` roda na máquina do aluno e protege a
execução dele. Estes testes rodam antes do commit e protegem o desenho do
laboratório — se alguém mexer nos parâmetros do gerador e a janela deslocada parar
de cruzar o limiar, a aula perde o ponto e o teste avisa.
"""

from __future__ import annotations

import numpy as np
import pytest

from fiap_ml_operations import config, data, drift

CFG = config.load_config()
FEATURES = config.feature_order()
BINS = int(CFG["drift"]["bins"])
EPSILON = float(CFG["drift"]["epsilon"])
LIMIAR = float(CFG["monitoring"]["alarm_threshold"])
DESIGN = CFG["drift"]["design"]


@pytest.fixture(scope="module")
def janelas():
    return data.generate()


def test_geracao_e_deterministica():
    """Duas gerações têm de ser byte a byte iguais, ou o hash publicado é ficção."""
    primeira = data.generate()
    segunda = data.generate()
    for nome, janela in primeira.items():
        assert janela.ids == segunda[nome].ids
        assert np.array_equal(janela.churn, segunda[nome].churn)
        for feature in FEATURES:
            assert np.array_equal(janela.features[feature], segunda[nome].features[feature])


def test_contagem_das_janelas(janelas):
    assert janelas["train"].rows == 2800
    assert janelas["validation"].rows == 600
    assert janelas["reference"].rows == 600
    assert janelas["production_baseline"].rows == 200
    assert janelas["production_shifted"].rows == 200


def test_identificadores_sao_unicos_entre_todas_as_janelas(janelas):
    todos = [obs_id for janela in janelas.values() for obs_id in janela.ids]
    assert len(todos) == len(set(todos))


def test_valores_respeitam_os_limites_do_contrato(janelas):
    for nome, janela in janelas.items():
        for feature in FEATURES:
            lo, hi = CFG["bounds"][feature]
            coluna = janela.features[feature]
            assert coluna.min() >= lo, f"{nome}:{feature} abaixo de {lo}"
            assert coluna.max() <= hi, f"{nome}:{feature} acima de {hi}"
            assert np.all(np.isfinite(coluna))


def test_rotulo_e_binario_e_prevalencia_esta_na_faixa(janelas):
    lo, hi = CFG["prevalence_range"]
    for nome, janela in janelas.items():
        assert set(np.unique(janela.churn)) <= {0, 1}
        assert lo <= janela.prevalence <= hi, f"{nome} com prevalência {janela.prevalence}"


def test_janela_deslocada_reflete_decisoes_de_negocio(janelas):
    """Cada diferença aqui corresponde a uma decisão da empresa, não a ruído."""
    base = janelas["production_baseline"].features
    shift = janelas["production_shifted"].features

    assert shift["monthly_charges"].mean() > base["monthly_charges"].mean() * 1.25, "reajuste"
    assert shift["support_calls_90d"].mean() > base["support_calls_90d"].mean() * 1.8, "sobrecarga"
    assert shift["payment_delay_days"].mean() > base["payment_delay_days"].mean() * 1.5, "cobrança"
    assert shift["usage_score"].mean() < base["usage_score"].mean() * 0.90, "experiência"
    assert shift["annual_contract"].mean() < base["annual_contract"].mean() * 0.60, "mix"


def test_tempo_de_casa_nao_muda_entre_as_janelas(janelas):
    """Nem tudo desloca — e isso é conteúdo.

    Se TODA feature acusasse drift, o aluno não aprenderia a olhar qual delas mudou.
    `tenure_months` é a base de clientes envelhecendo normalmente.
    """
    referencia = {f: janelas["reference"].features[f] for f in FEATURES}
    observada = {f: janelas["production_shifted"].features[f] for f in FEATURES}
    resultado = {
        d.feature: d.psi
        for d in drift.feature_drift(referencia, observada, FEATURES, bins=BINS, epsilon=EPSILON)
    }
    assert resultado["tenure_months"] < 0.10


def _psi_contra_referencia(janelas, nome_janela):
    referencia = {f: janelas["reference"].features[f] for f in FEATURES}
    observada = {f: janelas[nome_janela].features[f] for f in FEATURES}
    return drift.feature_drift(referencia, observada, FEATURES, bins=BINS, epsilon=EPSILON)


def test_janela_baseline_nao_dispara_o_alarme(janelas):
    """A janela saudável precisa ficar abaixo do limiar, ou o lab começa em ALARM."""
    drifts = _psi_contra_referencia(janelas, "production_baseline")
    psi_max = max(d.psi for d in drifts)
    assert psi_max < float(DESIGN["baseline_psi_max_below"]), f"PSI máximo {psi_max}"
    assert not drift.responsible_features(drifts, LIMIAR)


def test_janela_deslocada_cruza_o_limiar_com_folga(janelas):
    drifts = _psi_contra_referencia(janelas, "production_shifted")
    psi_max = max(d.psi for d in drifts)
    acima = drift.responsible_features(drifts, LIMIAR)

    assert psi_max >= float(DESIGN["shifted_psi_max_at_least"]), f"PSI máximo {psi_max}"
    assert len(acima) >= int(DESIGN["shifted_features_above_threshold_at_least"])


def test_mudanca_de_mix_de_contrato_e_detectada(janelas):
    """A feature binária tem de aparecer entre as responsáveis, não sumir."""
    drifts = {d.feature: d for d in _psi_contra_referencia(janelas, "production_shifted")}
    annual = drifts["annual_contract"]
    assert annual.binning == "categoria"
    assert annual.psi >= LIMIAR


def test_relacao_com_churn_muda_entre_os_dois_mundos():
    """Concept drift precisa existir de verdade, não só deslocamento de distribuição.

    Se só a distribuição mudasse, o modelo antigo continuaria correto e a queda de F1
    do laboratório não aconteceria. O teste compara os coeficientes que geram o
    rótulo nos dois mundos.
    """
    base = data.BASE_RELATIONSHIP
    shift = data.SHIFTED_RELATIONSHIP

    # As duas features que saturaram: perderam poder de discriminar.
    assert shift.calls < base.calls * 0.25
    assert shift.delay < base.delay * 0.25
    # Preço deixou de discriminar porque subiu para todos.
    assert shift.charges < base.charges * 0.20
    # Uso virou o driver dominante.
    assert abs(shift.usage) > abs(base.usage) * 1.30


def test_arquivos_escritos_batem_com_o_manifesto(tmp_path, monkeypatch):
    """O manifesto declara hashes; os arquivos precisam confirmá-los."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PREDICTIONS_DIR", tmp_path / "pred")
    monkeypatch.setattr(config, "EVIDENCE_DIR", tmp_path / "evid")

    manifest = data.write_all()

    for nome, info in manifest["files"].items():
        assert data.sha256(tmp_path / nome) == info["sha256"]

    assert manifest["lineage"]["source_lab"] == "02-ml-system"
    assert manifest["lineage"]["business_capability"] == "bora-fibra-churn"
    assert manifest["lineage"]["model_lineage"] == "churn-v1"


def test_canal_de_treino_nao_tem_cabecalho_e_traz_o_rotulo_primeiro(tmp_path, monkeypatch):
    """Contrato do XGBoost nativo: sem cabeçalho, alvo na coluna zero."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PREDICTIONS_DIR", tmp_path / "pred")
    monkeypatch.setattr(config, "EVIDENCE_DIR", tmp_path / "evid")
    data.write_all()

    primeira_linha = (tmp_path / "train.csv").read_text(encoding="utf-8").split("\n")[0]
    valores = primeira_linha.split(",")

    assert not any(c.isalpha() for c in primeira_linha), "há cabeçalho no canal de treino"
    assert len(valores) == len(FEATURES) + 1
    assert valores[0] in ("0", "1")


def test_janela_de_producao_nao_carrega_o_rotulo(tmp_path, monkeypatch):
    """O rótulo não existe no mundo quando a predição acontece."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PREDICTIONS_DIR", tmp_path / "pred")
    monkeypatch.setattr(config, "EVIDENCE_DIR", tmp_path / "evid")
    data.write_all()

    for arquivo in ("production_baseline.csv", "production_shifted.csv"):
        header, _ = data.read_csv(tmp_path / arquivo)
        assert header == [config.id_column(), *FEATURES]
        assert config.label_column() not in header


def test_ground_truth_cobre_exatamente_a_janela(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PREDICTIONS_DIR", tmp_path / "pred")
    monkeypatch.setattr(config, "EVIDENCE_DIR", tmp_path / "evid")
    data.write_all()

    for janela, arquivo in (
        ("production_baseline", "ground_truth_baseline.csv"),
        ("production_shifted", "ground_truth_shifted.csv"),
    ):
        ids, _ = data.read_window(tmp_path / f"{janela}.csv")
        rotulos = data.read_labels(tmp_path / arquivo)
        assert set(ids) == set(rotulos)
        assert set(rotulos.values()) <= {0, 1}
