"""Testes do PSI.

O PSI é o número que decide se o alarme dispara. Um erro aqui não aparece como
exceção: aparece como uma aula em que o alarme nunca toca, ou toca sempre.
"""

from __future__ import annotations

import numpy as np
import pytest

from fiap_ml_operations import drift


def test_distribuicoes_identicas_dao_psi_zero():
    amostra = np.linspace(0.0, 100.0, 500)
    valor, _, _, _ = drift.psi(amostra, amostra, bins=5)
    assert valor == pytest.approx(0.0, abs=1e-12)


def test_deslocamento_produz_psi_positivo():
    rng = np.random.default_rng(7)
    referencia = rng.normal(100.0, 20.0, 2000)
    deslocada = rng.normal(150.0, 20.0, 2000)
    valor, _, _, _ = drift.psi(referencia, deslocada, bins=5)
    assert valor > 0.5


def test_psi_categorico_e_simetrico():
    """No caminho categórico a régua é a união das categorias, que não tem ordem.

    Por isso, e só por isso, trocar os lados devolve o mesmo número.
    """
    a = np.array([0.0] * 60 + [1.0] * 40)
    b = np.array([0.0] * 25 + [1.0] * 75)
    ida, _, _, _ = drift.psi(a, b, bins=5)
    volta, _, _, _ = drift.psi(b, a, bins=5)
    assert ida == pytest.approx(volta, rel=1e-9)


def test_psi_continuo_e_direcional():
    """No caminho por quantil, a régua vem da REFERÊNCIA — trocar os lados muda o valor.

    Não é defeito: "referência" é um papel privilegiado, o mundo em que o modelo
    foi treinado. O teste existe para ninguém "consertar" isso achando que PSI
    deveria ser simétrico sempre — e para a assimetria estar documentada em algum
    lugar que falha se mudar.
    """
    rng = np.random.default_rng(11)
    a = rng.normal(50.0, 10.0, 1000)
    b = rng.normal(65.0, 25.0, 1000)
    ida, _, _, _ = drift.psi(a, b, bins=5)
    volta, _, _, _ = drift.psi(b, a, bins=5)
    assert ida != pytest.approx(volta, rel=1e-3)
    assert ida > 0 and volta > 0


def test_bins_vem_somente_da_referencia():
    """A janela observada não pode redefinir a régua.

    Se os bins fossem recalculados na janela observada, duas distribuições de
    formas iguais e centros diferentes cairiam nos mesmos quantis relativos e o PSI
    voltaria perto de zero — o deslocamento sumiria justo no caso que importa.
    """
    referencia = np.linspace(0.0, 100.0, 1000)
    deslocada = np.linspace(200.0, 300.0, 1000)

    valor, ref_share, obs_share, _ = drift.psi(referencia, deslocada, bins=5)

    # Toda a massa observada cai no último bin da referência.
    assert obs_share[-1] == pytest.approx(1.0, abs=1e-9)
    assert ref_share[0] > 0
    assert valor > 1.0


def test_feature_binaria_usa_caminho_categorico():
    """Regressão: variável binária dava PSI exatamente zero para sempre.

    Com bins por quantil, uma coluna 0/1 tem quantis repetidos; ao colapsar as
    duplicatas sobra um único bin e todo PSI vira zero. O monitoramento ficava cego
    à mudança de mix de `annual_contract` — que é uma das decisões de negócio que o
    laboratório precisa detectar.
    """
    referencia = np.array([0.0] * 65 + [1.0] * 35)
    observada = np.array([0.0] * 86 + [1.0] * 14)

    valor, ref_share, obs_share, binning = drift.psi(referencia, observada, bins=5)

    assert binning == "categoria"
    assert len(ref_share) == 2
    assert obs_share == pytest.approx([0.86, 0.14], abs=1e-9)
    assert valor > 0.20


def test_categoria_nova_na_janela_observada_entra_na_conta():
    """Um valor que nunca apareceu no treino é evidência, não sobra para descartar."""
    referencia = np.array([0.0, 1.0] * 50)
    observada = np.array([0.0, 1.0, 2.0] * 33 + [2.0])

    valor, ref_share, obs_share, binning = drift.psi(referencia, observada, bins=5, epsilon=1e-6)

    assert binning == "categoria"
    assert len(ref_share) == 3  # a união inclui a categoria 2
    assert ref_share[-1] == pytest.approx(1e-6)  # epsilon, não zero
    assert np.isfinite(valor) and valor > 0


def test_epsilon_evita_infinito_em_bin_vazio():
    referencia = np.linspace(0.0, 100.0, 1000)
    observada = np.full(200, 99.9)  # concentra tudo em um bin só

    valor, _, obs_share, _ = drift.psi(referencia, observada, bins=5, epsilon=1e-6)

    assert np.isfinite(valor)
    assert min(obs_share) == pytest.approx(1e-6)


def test_referencia_vazia_falha_com_mensagem():
    with pytest.raises(ValueError, match="referência está vazia"):
        drift.psi(np.array([]), np.array([1.0, 2.0]), bins=5)


def test_janela_observada_vazia_falha_com_mensagem():
    with pytest.raises(ValueError, match="janela observada está vazia"):
        drift.psi(np.linspace(0, 10, 100), np.array([]), bins=5)


def test_interpretacao_segue_as_faixas_do_mercado():
    assert drift.interpret(0.05) == "estável"
    assert drift.interpret(0.15) == "mudança moderada"
    assert drift.interpret(0.40) == "mudança relevante"


def test_features_responsaveis_saem_ordenadas_e_filtradas():
    drifts = [
        drift.FeatureDrift(feature="a", psi=0.05, bins=5),
        drift.FeatureDrift(feature="b", psi=0.90, bins=5),
        drift.FeatureDrift(feature="c", psi=0.25, bins=5),
    ]
    responsaveis = drift.responsible_features(drifts, threshold=0.20)
    assert [d.feature for d in responsaveis] == ["b", "c"]


def test_feature_drift_respeita_a_ordem_canonica():
    referencia = {"x": np.linspace(0, 1, 100), "y": np.linspace(0, 1, 100)}
    observada = {"x": np.linspace(0, 1, 100), "y": np.linspace(5, 6, 100)}
    resultado = drift.feature_drift(referencia, observada, ["y", "x"], bins=5)
    assert [d.feature for d in resultado] == ["y", "x"]
