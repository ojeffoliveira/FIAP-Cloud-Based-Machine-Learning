"""PSI — Population Stability Index.

O que o PSI responde: "a distribuição desta variável na janela observada ainda se
parece com a distribuição em que o modelo foi treinado?".

O que ele NÃO responde: se o modelo errou. PSI é medido sem olhar uma única vez
para o rótulo verdadeiro — é uma comparação de histogramas. Por isso o lab trata
drift como sinal, e a queda de F1 (que só chega com o ground truth) como
sentença.

Decisões de implementação que mudam o número:

1. **Bins vêm SÓ da referência**, por quantis, e são reaplicados à janela
   observada. Recalcular os quantis na janela observada normalizaria justamente o
   deslocamento que se quer medir — as duas distribuições sempre pareceriam
   iguais.
2. **Bins duplicados são colapsados.** Feature binária (`annual_contract`) tem
   quantis repetidos; sem colapsar, sobrariam bins de largura zero que nunca
   recebem massa e inflariam o PSI artificialmente.
3. **Epsilon substitui proporção zero.** Um bin vazio em qualquer dos lados
   levaria `log(0)` ou divisão por zero. O epsilon é declarado no config, não
   escondido no código, porque ele é o piso de sensibilidade da métrica.
4. **Variável discreta não usa quantil, usa frequência por categoria.** Uma
   feature binária tem quantis repetidos; ao colapsar as duplicatas sobraria um
   único bin, e o PSI daria exatamente zero para SEMPRE — o monitoramento ficaria
   cego à mudança de mix de `annual_contract`. Quando a referência tem no máximo
   `bins` valores distintos, cada valor é uma categoria.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Faixa de leitura consagrada na indústria para PSI. Não é lei estatística: é a
# convenção que o mercado adotou e que o aluno vai encontrar na literatura.
INTERPRETATION_BANDS = (
    (0.10, "estável"),
    (0.25, "mudança moderada"),
    (float("inf"), "mudança relevante"),
)


def interpret(psi: float) -> str:
    """Rótulo textual para um valor de PSI, na convenção usual do mercado."""
    for limit, label in INTERPRETATION_BANDS:
        if psi < limit:
            return label
    return "mudança relevante"


@dataclass(frozen=True)
class FeatureDrift:
    """PSI de uma feature, com os números que sustentam o valor."""

    feature: str
    psi: float
    bins: int
    # "quantil" para variável contínua, "categoria" para discreta. Vai para o JSON
    # de evidência: o mesmo número calculado de duas formas diferentes precisa
    # dizer qual das duas foi.
    binning: str = "quantil"
    reference_share: list[float] = field(default_factory=list)
    observed_share: list[float] = field(default_factory=list)

    @property
    def interpretation(self) -> str:
        return interpret(self.psi)


def is_discrete(reference: np.ndarray, bins: int) -> bool:
    """Poucos valores distintos na referência: tratar como categoria."""
    return int(np.unique(reference).size) <= bins


def quantile_edges(reference: np.ndarray, bins: int) -> np.ndarray:
    """Bordas de bin por quantil, calculadas na referência e sem duplicatas.

    As bordas externas viram -inf/+inf para que um valor da janela observada fora
    da faixa vista no treino caia no bin da ponta, em vez de ser descartado. Um
    valor nunca visto é exatamente o tipo de evidência que não pode desaparecer.
    """
    if reference.size == 0:
        raise ValueError("a referência está vazia: não há como calcular bins")

    quantiles = np.linspace(0.0, 1.0, bins + 1)
    edges = np.unique(np.quantile(reference, quantiles)).astype(float)

    if edges.size < 2:
        raise ValueError(
            "referência com um único valor distinto: use o caminho categórico"
        )

    edges[0] = -np.inf
    edges[-1] = np.inf
    return edges


def _shares_from_counts(counts: np.ndarray, epsilon: float, side: str) -> np.ndarray:
    """Contagens em proporção, com epsilon no lugar de zero.

    O `side` existe só para a mensagem de erro nomear o lado certo: "a referência
    está vazia" e "a janela observada está vazia" mandam o aluno investigar coisas
    diferentes.
    """
    total = counts.sum()
    if total == 0:
        raise ValueError(f"{side} está vazia: não há como calcular PSI")
    shares = counts / total
    return np.where(shares == 0.0, epsilon, shares)


def _continuous_shares(
    reference: np.ndarray, observed: np.ndarray, bins: int, epsilon: float
) -> tuple[np.ndarray, np.ndarray]:
    edges = quantile_edges(reference, bins)
    ref_counts, _ = np.histogram(reference, bins=edges)
    obs_counts, _ = np.histogram(observed, bins=edges)
    return (
        _shares_from_counts(ref_counts, epsilon, "a referência"),
        _shares_from_counts(obs_counts, epsilon, "a janela observada"),
    )


def _categorical_shares(
    reference: np.ndarray, observed: np.ndarray, epsilon: float
) -> tuple[np.ndarray, np.ndarray]:
    """Proporção por valor distinto, no conjunto união das duas janelas.

    A união importa: uma categoria que só aparece na janela observada é uma
    novidade do mundo, e precisa entrar na conta em vez de ser descartada.

    Note que a união é a MESMA independente da ordem dos argumentos — é por isso
    que o PSI categórico é simétrico, e o PSI por quantil não é.
    """
    categories = np.union1d(np.unique(reference), np.unique(observed))
    ref_counts = np.array([(reference == c).sum() for c in categories], dtype=float)
    obs_counts = np.array([(observed == c).sum() for c in categories], dtype=float)
    return (
        _shares_from_counts(ref_counts, epsilon, "a referência"),
        _shares_from_counts(obs_counts, epsilon, "a janela observada"),
    )


def psi(
    reference: np.ndarray,
    observed: np.ndarray,
    bins: int = 10,
    epsilon: float = 1e-6,
) -> tuple[float, list[float], list[float], str]:
    """PSI entre duas amostras da mesma variável.

    Devolve o valor, as duas distribuições em proporção e qual discretização foi
    usada, para o JSON de evidência poder mostrar de onde o número saiu.

    **O PSI não é simétrico quando a variável é contínua.** A régua (as bordas dos
    bins) vem da referência, então trocar referência por janela observada muda a
    régua e muda o número. Isso não é defeito: "referência" é um papel privilegiado,
    e é o mundo em que o modelo foi treinado. No caminho categórico a régua é a
    união das categorias, que não depende da ordem, e aí o valor é simétrico.
    """
    reference = np.asarray(reference, dtype=float)
    observed = np.asarray(observed, dtype=float)

    # Checado antes de escolher o caminho: uma referência vazia parece "discreta"
    # (zero valores distintos) e cairia no caminho categórico, produzindo uma
    # mensagem de erro que culpa a janela observada.
    if reference.size == 0:
        raise ValueError("a referência está vazia: não há como calcular bins")

    if is_discrete(reference, bins):
        binning = "categoria"
        ref_share, obs_share = _categorical_shares(reference, observed, epsilon)
    else:
        binning = "quantil"
        ref_share, obs_share = _continuous_shares(reference, observed, bins, epsilon)

    # PSI = soma sobre os bins de (obs - ref) * ln(obs / ref). Sempre >= 0.
    value = float(np.sum((obs_share - ref_share) * np.log(obs_share / ref_share)))
    return value, ref_share.tolist(), obs_share.tolist(), binning


def feature_drift(
    reference: dict[str, np.ndarray],
    observed: dict[str, np.ndarray],
    features: list[str],
    bins: int = 10,
    epsilon: float = 1e-6,
) -> list[FeatureDrift]:
    """PSI de cada feature, na ordem canônica recebida."""
    results: list[FeatureDrift] = []
    for name in features:
        value, ref_share, obs_share, binning = psi(
            reference[name], observed[name], bins=bins, epsilon=epsilon
        )
        results.append(
            FeatureDrift(
                feature=name,
                psi=value,
                bins=len(ref_share),
                binning=binning,
                reference_share=[round(x, 6) for x in ref_share],
                observed_share=[round(x, 6) for x in obs_share],
            )
        )
    return results


def responsible_features(
    drifts: list[FeatureDrift], threshold: float
) -> list[FeatureDrift]:
    """Features acima do limiar, da maior para a menor.

    É o que o `make drift` imprime: o aluno precisa sair do comando sabendo QUAL
    variável mudou, não só que "houve drift".
    """
    above = [d for d in drifts if d.psi >= threshold]
    return sorted(above, key=lambda d: d.psi, reverse=True)
