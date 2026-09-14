"""Gerador determinístico do dataset do Trabalho Final — Bora Fibra.

Nenhum parâmetro de distribuição vive aqui: `config/scenario.yaml` é a fonte
única de verdade (seed, splits, população, coeficientes, história do shift).
Este módulo só sabe *como* sortear e serializar — os números de *o quê*
sortear vêm sempre de `cfg.scenario`.

Duas janelas de produção, um propósito pedagógico único (mesma linhagem do
lab 04-ml-operations/01-observability-drift-response, agora com um segundo
workload e uma calibração própria — ver `04_DATASET_AND_ACCEPTANCE.md`):

* `production_baseline` ainda se parece com o mundo em que o modelo treinou;
* `production_shifted` vem depois do reajuste de mensalidade e da mudança de
  política comercial da mesma semana — muda a DISTRIBUIÇÃO das features
  (o que o PSI detecta, sem olhar rótulo) e a RELAÇÃO entre feature e churn
  (concept shift, o que derruba o F1 e só se confirma quando o ground truth
  atrasado chega).

Além dessas duas, mais três: `campaign_features` (lista noturna da campanha
de retenção) e `atendimento` (40 clientes fixos, alto/baixo/intermediário,
para consulta durante a ligação) — os dois workloads do enunciado — e
`reference`, a régua de comparação do drift.

Ordem dos sorteios abaixo faz parte do contrato de determinismo: reordenar
muda o fluxo do RNG e, com isso, o hash de todo arquivo gerado depois do
ponto alterado.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import Config

# --------------------------------------------------------------------------- #
# Saída: stdout carrega resultado, stderr carrega progresso.
# --------------------------------------------------------------------------- #


def log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


def emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


# Nomes de arquivo na ordem em que o contrato de dados os exige — a mesma
# ordem em que `generate()` os escreve.
FILES: tuple[str, ...] = (
    "train.csv",
    "validation.csv",
    "reference.csv",
    "production_baseline.csv",
    "production_shifted.csv",
    "campaign_features.csv",
    "ground_truth_baseline.csv",
    "ground_truth_shifted.csv",
    "atendimento.csv",
)

MANIFEST_FILE = "dataset_manifest.json"


# --------------------------------------------------------------------------- #
# Estruturas de população e relação, lidas de config/scenario.yaml
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Population:
    """Parâmetros de distribuição de uma janela de clientes."""

    charges_mean: float
    charges_sd: float
    calls_lambda: float
    delay_scale: float
    usage_mean: float
    usage_sd: float
    annual_contract_p: float
    premium_plan_p: float
    # Faixa de tenure própria: só os perfis de atendimento a customizam (um
    # cliente de alto risco tende a ser recente, um de baixo risco tende a
    # ser antigo). As demais janelas usam a faixa de `bounds.tenure_months`.
    tenure_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class Relationship:
    """Coeficientes da logística geradora — o "porquê" de cada churn."""

    intercept: float
    tenure_months: float
    monthly_charges: float
    support_calls_90d: float
    payment_delay_days: float
    usage_score: float
    annual_contract: float
    premium_plan: float


def _population_from_dict(raw: dict[str, Any]) -> Population:
    tenure_range = raw.get("tenure_range")
    return Population(
        charges_mean=float(raw["charges_mean"]),
        charges_sd=float(raw["charges_sd"]),
        calls_lambda=float(raw["calls_lambda"]),
        delay_scale=float(raw["delay_scale"]),
        usage_mean=float(raw["usage_mean"]),
        usage_sd=float(raw["usage_sd"]),
        annual_contract_p=float(raw["annual_contract_p"]),
        premium_plan_p=float(raw["premium_plan_p"]),
        tenure_range=(int(tenure_range[0]), int(tenure_range[1])) if tenure_range else None,
    )


def _relationship_from_dict(raw: dict[str, Any]) -> Relationship:
    return Relationship(
        intercept=float(raw["intercept"]),
        tenure_months=float(raw["tenure_months"]),
        monthly_charges=float(raw["monthly_charges"]),
        support_calls_90d=float(raw["support_calls_90d"]),
        payment_delay_days=float(raw["payment_delay_days"]),
        usage_score=float(raw["usage_score"]),
        annual_contract=float(raw["annual_contract"]),
        premium_plan=float(raw["premium_plan"]),
    )


def _bounds(cfg: Config) -> dict[str, tuple[float, float]]:
    return {k: (float(v[0]), float(v[1])) for k, v in cfg.scenario["bounds"].items()}


def _decimals(cfg: Config) -> dict[str, int]:
    return {k: int(v) for k, v in cfg.scenario["decimals"].items()}


def _center(cfg: Config) -> dict[str, tuple[float, float]]:
    return {k: (float(v[0]), float(v[1])) for k, v in cfg.scenario["center"].items()}


# --------------------------------------------------------------------------- #
# Sorteio
# --------------------------------------------------------------------------- #


def _draw_features(
    rng: np.random.Generator,
    rows: int,
    pop: Population,
    bounds: dict[str, tuple[float, float]],
) -> dict[str, np.ndarray]:
    """Sorteia as sete features na ordem do contrato. A ordem dos `rng.*`
    abaixo é parte do contrato de determinismo — não reordenar."""
    lo_t, hi_t = pop.tenure_range or (int(bounds["tenure_months"][0]), int(bounds["tenure_months"][1]))
    lo_c, hi_c = bounds["monthly_charges"]
    lo_s, hi_s = bounds["support_calls_90d"]
    lo_p, hi_p = bounds["payment_delay_days"]
    lo_u, hi_u = bounds["usage_score"]

    return {
        "tenure_months": rng.integers(lo_t, hi_t + 1, size=rows).astype(float),
        "monthly_charges": np.clip(rng.normal(pop.charges_mean, pop.charges_sd, rows), lo_c, hi_c),
        "support_calls_90d": np.clip(rng.poisson(pop.calls_lambda, rows).astype(float), lo_s, hi_s),
        "payment_delay_days": np.clip(rng.exponential(pop.delay_scale, rows), lo_p, hi_p),
        "usage_score": np.clip(rng.normal(pop.usage_mean, pop.usage_sd, rows), lo_u, hi_u),
        "annual_contract": (rng.random(rows) < pop.annual_contract_p).astype(float),
        "premium_plan": (rng.random(rows) < pop.premium_plan_p).astype(float),
    }


def systematic_probability(
    features: dict[str, np.ndarray], rel: Relationship, center: dict[str, tuple[float, float]]
) -> np.ndarray:
    """A parte determinística do processo gerador: a probabilidade de churn
    ANTES de sortear o rótulo. Exposta (não só interna) porque o contrato de
    dados usa a mesma conta, com a relação `base`, para provar a separação
    de risco dos 40 clientes de atendimento — sem precisar de um modelo
    treinado para uma verificação que roda antes de qualquer treino."""

    def z(name: str) -> np.ndarray:
        c, s = center[name]
        return (features[name] - c) / s

    logit = (
        rel.intercept
        + rel.tenure_months * z("tenure_months")
        + rel.monthly_charges * z("monthly_charges")
        + rel.support_calls_90d * z("support_calls_90d")
        + rel.payment_delay_days * z("payment_delay_days")
        + rel.usage_score * z("usage_score")
        + rel.annual_contract * features["annual_contract"]
        + rel.premium_plan * features["premium_plan"]
    )
    return 1.0 / (1.0 + np.exp(-logit))


def _draw_churn(
    rng: np.random.Generator,
    features: dict[str, np.ndarray],
    rel: Relationship,
    center: dict[str, tuple[float, float]],
) -> np.ndarray:
    """Sorteia o rótulo a partir da relação vigente. `rng.random() < p` é
    pura manipulação de bits — determinístico em qualquer plataforma, ao
    contrário de `rng.binomial`, que pode variar um ULP entre implementações
    SIMD de exp/log."""
    probability = systematic_probability(features, rel, center)
    return (rng.random(probability.size) < probability).astype(int)


def _round_features(features: dict[str, np.ndarray], decimals: dict[str, int]) -> dict[str, np.ndarray]:
    return {name: np.round(values, decimals[name]) for name, values in features.items()}


def _format(name: str, value: float, decimals: dict[str, int]) -> str:
    return f"{int(round(value))}" if decimals[name] == 0 else f"{value:.{decimals[name]}f}"


@dataclass(frozen=True)
class Window:
    """Uma janela nomeada: ids (pode ser vazio), features na ordem canônica, rótulo (pode ser None)."""

    name: str
    ids: list[str]
    features: dict[str, np.ndarray]
    churn: np.ndarray | None

    @property
    def rows(self) -> int:
        first = next(iter(self.features.values()))
        return int(first.shape[0])

    @property
    def prevalence(self) -> float | None:
        return float(self.churn.mean()) if self.churn is not None else None


# --------------------------------------------------------------------------- #
# Geração das janelas
# --------------------------------------------------------------------------- #


def _build_windows(cfg: Config) -> dict[str, Window]:
    scenario = cfg.scenario
    bounds = _bounds(cfg)
    decimals = _decimals(cfg)
    center = _center(cfg)
    features_order = cfg.feature_order

    base_pop = _population_from_dict(scenario["population"]["base"])
    shifted_pop = _population_from_dict(scenario["population"]["shifted"])
    base_rel = _relationship_from_dict(scenario["relationship"]["base"])
    shifted_rel = _relationship_from_dict(scenario["relationship"]["shifted"])

    rng = np.random.default_rng(cfg.seed)

    # 1. O mundo em que o modelo treina: 4.000 clientes antes do reajuste.
    world_rows = int(scenario["dataset"]["world_rows"])
    world_features = _round_features(_draw_features(rng, world_rows, base_pop, bounds), decimals)
    world_churn = _draw_churn(rng, world_features, base_rel, center)

    splits = scenario["dataset"]["splits"]
    n_train, n_validation = int(splits["train"]), int(splits["validation"])
    order = rng.permutation(world_rows)
    slices = {
        "train": order[:n_train],
        "validation": order[n_train : n_train + n_validation],
        "reference": order[n_train + n_validation :],
    }

    windows: dict[str, Window] = {}
    for name, idx in slices.items():
        windows[name] = Window(
            name=name,
            ids=[f"{_PREFIX[name]}-{i:06d}" for i in range(1, len(idx) + 1)],
            features={f: world_features[f][idx] for f in features_order},
            churn=world_churn[idx],
        )

    # 2. As duas janelas de produção do piloto — mesma ordem de sorteio
    # sempre: baseline primeiro, shifted depois.
    production = scenario["dataset"]["production_windows"]
    for name, pop, rel, count in (
        ("production_baseline", base_pop, base_rel, int(production["baseline_rows"])),
        ("production_shifted", shifted_pop, shifted_rel, int(production["shifted_rows"])),
    ):
        feats = _round_features(_draw_features(rng, count, pop, bounds), decimals)
        churn = _draw_churn(rng, feats, rel, center)
        windows[name] = Window(
            name=name,
            ids=[f"{_PREFIX[name]}-{i:06d}" for i in range(1, count + 1)],
            features={f: feats[f] for f in features_order},
            churn=churn,
        )

    # 3. Lista noturna da campanha — mesmo mundo de antes do reajuste: é a
    # base de clientes ativa que a campanha prioriza, não uma amostra da
    # janela de incidente.
    campaign_rows = int(scenario["dataset"]["campaign_rows"])
    campaign_feats = _round_features(_draw_features(rng, campaign_rows, base_pop, bounds), decimals)
    windows["campaign_features"] = Window(
        name="campaign_features",
        ids=[f"camp-{i:06d}" for i in range(1, campaign_rows + 1)],
        features={f: campaign_feats[f] for f in features_order},
        churn=None,
    )

    # 4. Atendimento — 40 clientes fixos, três perfis, na ordem declarada em
    # scenario.yaml (alto risco, baixo risco, intermediário).
    profiles = scenario["atendimento"]["profiles"]
    atendimento_features: dict[str, list[np.ndarray]] = {f: [] for f in features_order}
    atendimento_ids: list[str] = []
    seq = 1
    for profile in profiles:
        pop = _population_from_dict(profile)
        count = int(profile["count"])
        feats = _round_features(_draw_features(rng, count, pop, bounds), decimals)
        for f in features_order:
            atendimento_features[f].append(feats[f])
        atendimento_ids.extend(f"atd-{seq + i:06d}" for i in range(count))
        seq += count
    windows["atendimento"] = Window(
        name="atendimento",
        ids=atendimento_ids,
        features={f: np.concatenate(atendimento_features[f]) for f in features_order},
        churn=None,
    )

    return windows


_PREFIX = {
    "train": "trn",
    "validation": "val",
    "reference": "ref",
    "production_baseline": "pb",
    "production_shifted": "ps",
}


# --------------------------------------------------------------------------- #
# Serialização
# --------------------------------------------------------------------------- #


def _write_training_csv(path: Path, window: Window, features: list[str], decimals: dict[str, int]) -> None:
    """Sem cabeçalho, rótulo na primeira coluna — o contrato do XGBoost nativo
    do SageMaker. Sem id: o id nunca entra no modelo."""
    assert window.churn is not None
    lines = []
    for i in range(window.rows):
        values = [str(int(window.churn[i]))]
        values += [_format(name, window.features[name][i], decimals) for name in features]
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_labeled_csv(
    path: Path, window: Window, features: list[str], decimals: dict[str, int], cfg: Config
) -> None:
    """Cabeçalho, id e rótulo — referência de drift, uso interno do trabalho
    final, nunca o canal de treino."""
    assert window.churn is not None
    lines = [",".join([cfg.id_column, *features, cfg.label_column])]
    for i in range(window.rows):
        values = [window.ids[i]]
        values += [_format(name, window.features[name][i], decimals) for name in features]
        values.append(str(int(window.churn[i])))
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_inference_csv(
    path: Path, window: Window, features: list[str], decimals: dict[str, int], cfg: Config
) -> None:
    """Id + features, SEM rótulo — o payload que o endpoint/batch realmente
    vê. O rótulo dessas linhas ainda não existe no mundo real; guardá-lo
    aqui vazaria o futuro para dentro do contrato de inferência."""
    lines = [",".join([cfg.id_column, *features])]
    for i in range(window.rows):
        values = [window.ids[i]]
        values += [_format(name, window.features[name][i], decimals) for name in features]
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_ground_truth_csv(path: Path, window: Window, cfg: Config) -> None:
    """Os rótulos que "chegaram depois" — arquivo separado de propósito, para
    o atraso do ground truth ser visível no próprio layout dos dados."""
    assert window.churn is not None
    lines = [",".join([cfg.id_column, cfg.label_column])]
    for i in range(window.rows):
        lines.append(f"{window.ids[i]},{int(window.churn[i])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate(cfg: Config) -> dict[str, Any]:
    """Gera as nove janelas e escreve `artifacts/data/*.csv` + o manifesto.
    Determinístico pela seed de `config/scenario.yaml`. Devolve o mesmo
    resumo que é gravado em `dataset_manifest.json`."""
    features = cfg.feature_order
    decimals = _decimals(cfg)
    out = cfg.data_dir
    out.mkdir(parents=True, exist_ok=True)

    log(f"[data] semente={cfg.seed} destino={out}")
    windows = _build_windows(cfg)

    _write_training_csv(out / "train.csv", windows["train"], features, decimals)
    _write_training_csv(out / "validation.csv", windows["validation"], features, decimals)
    _write_labeled_csv(out / "reference.csv", windows["reference"], features, decimals, cfg)
    _write_inference_csv(out / "production_baseline.csv", windows["production_baseline"], features, decimals, cfg)
    _write_inference_csv(out / "production_shifted.csv", windows["production_shifted"], features, decimals, cfg)
    _write_inference_csv(out / "campaign_features.csv", windows["campaign_features"], features, decimals, cfg)
    _write_ground_truth_csv(out / "ground_truth_baseline.csv", windows["production_baseline"], cfg)
    _write_ground_truth_csv(out / "ground_truth_shifted.csv", windows["production_shifted"], cfg)
    _write_inference_csv(out / "atendimento.csv", windows["atendimento"], features, decimals, cfg)

    for name in FILES:
        log(f"[data] escrito {name} ({(out / name).stat().st_size} bytes)")

    manifest: dict[str, Any] = {
        "schema_version": cfg.scenario.get("schema_version"),
        "lineage": cfg.scenario.get("lineage"),
        "seed": cfg.seed,
        "label": cfg.label_column,
        "id_column": cfg.id_column,
        "feature_order": features,
        "bounds": cfg.scenario["bounds"],
        "windows": {
            name: {"rows": w.rows, "prevalence": round(w.prevalence, 6) if w.prevalence is not None else None}
            for name, w in windows.items()
        },
        "files": {name: {"sha256": sha256(out / name), "bytes": (out / name).stat().st_size} for name in FILES},
    }

    (out / MANIFEST_FILE).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log(f"[data] manifesto gravado em {out / MANIFEST_FILE}")
    return manifest


def fingerprints(cfg: Config) -> dict[str, str]:
    """sha256 de cada um dos nove arquivos, lido do disco agora — é contra
    isso que o contrato de dados compara o que `generate()` registrou no
    manifesto, para provar que ninguém editou um CSV depois de gerado."""
    out = cfg.data_dir
    return {name: sha256(out / name) for name in FILES if (out / name).exists()}


# --------------------------------------------------------------------------- #
# CLI — subcomando `data` de scripts/final.py
# --------------------------------------------------------------------------- #


def cmd_data(cfg: Config, args: Any) -> int:
    """`make data`: gera o dataset e imprime o manifesto em stdout."""
    manifest = generate(cfg)
    emit(manifest)
    return 0
