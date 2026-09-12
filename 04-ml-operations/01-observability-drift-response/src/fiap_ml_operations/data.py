"""Dataset do Lab 04.1 — a mesma capacidade de churn da Bora Fibra, agora em operação.

Três blocos de dados, um propósito cada:

* **base (4000 linhas)** — o que o Lab 02 já tinha: treino, validação e uma fatia
  de referência. Mesma seed, mesmo schema, mesma ordem de features. É o mundo em
  que o modelo aprendeu.
* **production_baseline (200)** — clientes que chegaram ao endpoint depois do
  go-live e ainda se parecem com aquele mundo.
* **production_shifted (200)** — clientes depois das mudanças comerciais da Bora
  Fibra: reajuste de preço, novo processo de cobrança, atendimento sobrecarregado.

A janela deslocada muda DUAS coisas, e a distinção é o coração do laboratório:

1. a **distribuição das features** (é isto que o PSI detecta, sem olhar rótulo);
2. a **relação entre features e churn** (é isto que derruba o F1, e só aparece
   quando o ground truth chega).

Poderia mudar só a distribuição — e aí o PSI dispararia com o modelo continuando
correto. É justamente o cenário que o aluno precisa saber que existe, e é por isso
que drift é tratado como sinal e não como sentença. Aqui as duas coisas mudam
juntas porque a aula precisa fechar o ciclo até a decisão; o README diz
explicitamente que uma não implica a outra.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from . import config

# --------------------------------------------------------------------------- #
# Parâmetros da população
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Population:
    """Parâmetros de distribuição de uma janela de clientes.

    Os valores centrais têm significado de negócio: `charges_mean` é o ticket
    médio, `calls_lambda` é quantos chamados um cliente abre em 90 dias.
    """

    charges_mean: float
    charges_sd: float
    calls_lambda: float
    delay_scale: float
    usage_mean: float
    usage_sd: float
    annual_contract_p: float
    premium_plan_p: float


# O mundo em que o modelo foi treinado.
BASE_POPULATION = Population(
    charges_mean=110.0,
    charges_sd=38.0,
    calls_lambda=2.0,
    delay_scale=4.0,
    usage_mean=65.0,
    usage_sd=18.0,
    annual_contract_p=0.35,
    premium_plan_p=0.30,
)

# O mundo depois das decisões comerciais da Bora Fibra. Cada campo alterado
# corresponde a uma decisão concreta da empresa, não a ruído.
SHIFTED_POPULATION = replace(
    BASE_POPULATION,
    charges_mean=158.0,  # reajuste de preços e novos planos
    charges_sd=44.0,
    calls_lambda=5.2,  # atendimento sobrecarregado
    delay_scale=9.5,  # novo processo de cobrança atrasa pagamentos
    usage_mean=51.0,  # experiência percebida piorou
    usage_sd=19.0,
    annual_contract_p=0.14,  # mix migrou para contrato mensal
    premium_plan_p=0.46,  # empurrão comercial no plano premium
)


@dataclass(frozen=True)
class ChurnRelationship:
    """Como cada feature empurra a probabilidade de churn.

    Coeficientes de uma logística sobre features centradas e escaladas. O sinal é
    a leitura de negócio: tempo de casa protege (negativo), chamado de suporte
    machuca (positivo).
    """

    intercept: float
    tenure: float
    charges: float
    calls: float
    delay: float
    usage: float
    annual_contract: float
    premium_plan: float


# A relação que o modelo aprendeu no Lab 02. Sinal forte de propósito: o lab
# precisa de um modelo que realmente funcionava antes, senão a degradação depois
# não tem de onde cair.
BASE_RELATIONSHIP = ChurnRelationship(
    intercept=-0.40,
    tenure=-2.00,
    charges=1.80,
    calls=2.00,
    delay=1.40,
    usage=-1.80,
    annual_contract=-2.00,
    premium_plan=-0.60,
)

# A relação depois das mudanças comerciais — concept drift.
#
# A história de negócio, que é o que o aluno precisa levar embora: o reajuste e o
# novo processo de cobrança fizeram TODO MUNDO ligar para o suporte e atrasar
# pagamento. As duas variáveis que eram o melhor sinal de saída **saturaram**:
# quando todo cliente liga, ligar deixa de distinguir quem vai sair. O mesmo vale
# para preço, que subiu para a base inteira, e para a fidelidade, que venceu.
#
# Quem sai agora é quem parou de usar o serviço — `usage_score` vira o driver
# dominante, e era a feature de menor peso relativo na decisão do modelo antigo.
#
# Consequência que o dashboard vai mostrar: o modelo vê preço alto, chamado alto e
# atraso alto e prevê churn para quase todo mundo. A taxa prevista sobe, a
# probabilidade média sobe, e a precisão desaba — o modelo virou uma máquina de
# falso positivo, sem que nenhuma métrica de infraestrutura piscasse.
SHIFTED_RELATIONSHIP = replace(
    BASE_RELATIONSHIP,
    intercept=-3.40,
    tenure=-0.60,
    charges=0.10,
    calls=0.15,
    delay=0.10,
    usage=-2.60,
    annual_contract=-0.30,
    premium_plan=-0.20,
)

# Escalas de centralização, fixas nos dois mundos. Se elas mudassem junto com a
# população, o logit deixaria de ser comparável entre as janelas.
CENTER = {
    "tenure_months": (36.0, 36.0),
    "monthly_charges": (110.0, 60.0),
    "support_calls_90d": (2.0, 3.0),
    "payment_delay_days": (4.0, 8.0),
    "usage_score": (65.0, 18.0),
}

# Casas decimais por coluna. Fixas para o arquivo ser byte a byte reprodutível:
# hash de dataset só serve como contrato se a serialização não variar.
DECIMALS = {
    "tenure_months": 0,
    "monthly_charges": 2,
    "support_calls_90d": 0,
    "payment_delay_days": 1,
    "usage_score": 1,
    "annual_contract": 0,
    "premium_plan": 0,
}


# --------------------------------------------------------------------------- #
# Geração
# --------------------------------------------------------------------------- #


def _draw_features(
    rng: np.random.Generator, rows: int, pop: Population, bounds: dict[str, Any]
) -> dict[str, np.ndarray]:
    """Sorteia as sete features dentro dos limites do contrato."""
    lo_t, hi_t = bounds["tenure_months"]
    lo_c, hi_c = bounds["monthly_charges"]
    lo_s, hi_s = bounds["support_calls_90d"]
    lo_p, hi_p = bounds["payment_delay_days"]
    lo_u, hi_u = bounds["usage_score"]

    return {
        "tenure_months": rng.integers(lo_t, hi_t + 1, size=rows).astype(float),
        "monthly_charges": np.clip(
            rng.normal(pop.charges_mean, pop.charges_sd, rows), lo_c, hi_c
        ),
        "support_calls_90d": np.clip(
            rng.poisson(pop.calls_lambda, rows).astype(float), lo_s, hi_s
        ),
        "payment_delay_days": np.clip(
            rng.exponential(pop.delay_scale, rows), lo_p, hi_p
        ),
        "usage_score": np.clip(rng.normal(pop.usage_mean, pop.usage_sd, rows), lo_u, hi_u),
        "annual_contract": (rng.random(rows) < pop.annual_contract_p).astype(float),
        "premium_plan": (rng.random(rows) < pop.premium_plan_p).astype(float),
    }


def _draw_churn(
    rng: np.random.Generator,
    features: dict[str, np.ndarray],
    rel: ChurnRelationship,
) -> np.ndarray:
    """Sorteia o rótulo a partir da relação vigente naquele mundo."""

    def z(name: str) -> np.ndarray:
        center, scale = CENTER[name]
        return (features[name] - center) / scale

    logit = (
        rel.intercept
        + rel.tenure * z("tenure_months")
        + rel.charges * z("monthly_charges")
        + rel.calls * z("support_calls_90d")
        + rel.delay * z("payment_delay_days")
        + rel.usage * z("usage_score")
        + rel.annual_contract * features["annual_contract"]
        + rel.premium_plan * features["premium_plan"]
    )
    probability = 1.0 / (1.0 + np.exp(-logit))
    return (rng.random(logit.size) < probability).astype(int)


def _round_features(features: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {name: np.round(values, DECIMALS[name]) for name, values in features.items()}


def _format(name: str, value: float) -> str:
    decimals = DECIMALS[name]
    return f"{int(round(value))}" if decimals == 0 else f"{value:.{decimals}f}"


@dataclass(frozen=True)
class Window:
    """Uma janela nomeada de clientes: ids, features na ordem canônica, rótulo."""

    name: str
    ids: list[str]
    features: dict[str, np.ndarray]
    churn: np.ndarray

    @property
    def rows(self) -> int:
        return len(self.ids)

    @property
    def prevalence(self) -> float:
        return float(self.churn.mean())


def generate() -> dict[str, Window]:
    """Gera as janelas do laboratório. Determinístico pela seed do config."""
    cfg = config.load_config()
    dataset = cfg["dataset"]
    bounds = cfg["bounds"]
    rng = np.random.default_rng(int(dataset["seed"]))

    rows = int(dataset["rows"])
    base_features = _round_features(_draw_features(rng, rows, BASE_POPULATION, bounds))
    base_churn = _draw_churn(rng, base_features, BASE_RELATIONSHIP)
    base_ids = [f"obs-{i:06d}" for i in range(1, rows + 1)]

    split = dataset["split"]
    n_train = int(round(rows * float(split["train"])))
    n_validation = int(round(rows * float(split["validation"])))

    order = rng.permutation(rows)
    slices = {
        "train": order[:n_train],
        "validation": order[n_train : n_train + n_validation],
        "reference": order[n_train + n_validation :],
    }

    windows: dict[str, Window] = {}
    for name, index in slices.items():
        windows[name] = Window(
            name=name,
            ids=[base_ids[i] for i in index],
            features={k: v[index] for k, v in base_features.items()},
            churn=base_churn[index],
        )

    production = dataset["production_windows"]
    for name, pop, rel, prefix, count in (
        (
            "production_baseline",
            BASE_POPULATION,
            BASE_RELATIONSHIP,
            "prod-b",
            int(production["baseline_rows"]),
        ),
        (
            "production_shifted",
            SHIFTED_POPULATION,
            SHIFTED_RELATIONSHIP,
            "prod-s",
            int(production["shifted_rows"]),
        ),
    ):
        features = _round_features(_draw_features(rng, count, pop, bounds))
        windows[name] = Window(
            name=name,
            ids=[f"{prefix}-{i:06d}" for i in range(1, count + 1)],
            features=features,
            churn=_draw_churn(rng, features, rel),
        )

    return windows


# --------------------------------------------------------------------------- #
# Serialização
# --------------------------------------------------------------------------- #


def _write_training_csv(path: Path, window: Window, features: list[str]) -> None:
    """CSV do canal de treino: sem cabeçalho, rótulo na primeira coluna.

    Não é escolha de estilo — é o contrato do XGBoost nativo do SageMaker. Com
    cabeçalho, a primeira linha entraria como observação; com o rótulo em outra
    posição, o algoritmo treinaria contra a coluna errada sem reclamar.
    """
    lines = []
    for i in range(window.rows):
        values = [str(int(window.churn[i]))]
        values += [_format(name, window.features[name][i]) for name in features]
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_labeled_csv(path: Path, window: Window, features: list[str]) -> None:
    """CSV com cabeçalho, id e rótulo — para uso do próprio lab, nunca do treino."""
    cfg_id, cfg_label = config.id_column(), config.label_column()
    lines = [",".join([cfg_id, *features, cfg_label])]
    for i in range(window.rows):
        values = [window.ids[i]]
        values += [_format(name, window.features[name][i]) for name in features]
        values.append(str(int(window.churn[i])))
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_inference_csv(path: Path, window: Window, features: list[str]) -> None:
    """Janela de produção como o endpoint a vê: id + features, SEM rótulo.

    O rótulo dessas linhas ainda não existe no mundo real — é o que o
    `make ground-truth` vai buscar depois. Guardá-lo aqui seria vazar o futuro
    para dentro do payload de inferência.
    """
    lines = [",".join([config.id_column(), *features])]
    for i in range(window.rows):
        values = [window.ids[i]]
        values += [_format(name, window.features[name][i]) for name in features]
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_ground_truth_csv(path: Path, window: Window) -> None:
    """Os rótulos que 'chegaram depois'. Arquivo separado de propósito."""
    lines = [",".join([config.id_column(), config.label_column()])]
    for i in range(window.rows):
        lines.append(f"{window.ids[i]},{int(window.churn[i])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_all() -> dict[str, Any]:
    """Escreve o dataset em artifacts/data/ e devolve o manifesto."""
    cfg = config.load_config()
    features = config.feature_order()
    config.ensure_dirs()
    out = config.DATA_DIR

    windows = generate()

    _write_training_csv(out / "train.csv", windows["train"], features)
    _write_training_csv(out / "validation.csv", windows["validation"], features)
    _write_labeled_csv(out / "reference.csv", windows["reference"], features)
    _write_inference_csv(
        out / "production_baseline.csv", windows["production_baseline"], features
    )
    _write_inference_csv(
        out / "production_shifted.csv", windows["production_shifted"], features
    )
    _write_ground_truth_csv(
        out / "ground_truth_baseline.csv", windows["production_baseline"]
    )
    _write_ground_truth_csv(
        out / "ground_truth_shifted.csv", windows["production_shifted"]
    )

    files = [
        "train.csv",
        "validation.csv",
        "reference.csv",
        "production_baseline.csv",
        "production_shifted.csv",
        "ground_truth_baseline.csv",
        "ground_truth_shifted.csv",
    ]

    manifest = {
        "schema_version": cfg["schema_version"],
        # A linhagem é afirmada aqui e conferida pelo contrato de dados: é como o
        # lab prova que não trocou de caso de negócio no meio do curso.
        "lineage": {
            "source_lab": cfg["lineage"]["source_lab"],
            "business_capability": cfg["lineage"]["business_capability"],
            "model_lineage": cfg["lineage"]["model_lineage"],
        },
        "seed": int(cfg["dataset"]["seed"]),
        "label": config.label_column(),
        "id_column": config.id_column(),
        "feature_order": features,
        "bounds": cfg["bounds"],
        "windows": {
            name: {
                "rows": window.rows,
                "prevalence": round(window.prevalence, 6),
            }
            for name, window in windows.items()
        },
        "files": {name: {"sha256": sha256(out / name)} for name in files},
    }

    (out / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """Leitor mínimo de CSV com cabeçalho. Evita dependência de pandas no lab."""
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    header = lines[0].split(",")
    return header, [line.split(",") for line in lines[1:]]


def read_window(path: Path) -> tuple[list[str], dict[str, np.ndarray]]:
    """Devolve (ids, features) de um CSV de janela, na ordem canônica."""
    features = config.feature_order()
    header, rows = read_csv(path)
    index = {name: header.index(name) for name in features}
    id_index = header.index(config.id_column())

    ids = [row[id_index] for row in rows]
    values = {
        name: np.array([float(row[index[name]]) for row in rows]) for name in features
    }
    return ids, values


def read_labels(path: Path) -> dict[str, int]:
    """Mapa id -> rótulo, para juntar predição com ground truth."""
    header, rows = read_csv(path)
    id_index = header.index(config.id_column())
    label_index = header.index(config.label_column())
    return {row[id_index]: int(row[label_index]) for row in rows}
