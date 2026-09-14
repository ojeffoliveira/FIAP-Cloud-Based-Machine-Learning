"""PSI de dados e de predicao das duas janelas de producao da Bora Fibra.

Mesma matematica do lab `04-ml-operations/01-observability-drift-response`
(`fiap_ml_operations.drift`): bins por quantil calculados SO na referencia
(`artifacts/data/reference.csv` -- o mundo em que o modelo foi treinado),
colapso de bin duplicado, caminho categorico quando a referencia tem poucos
valores distintos, epsilon no lugar de proporcao zero. Nenhuma dessa
matematica e reinventada aqui: o dataset (A2) foi calibrado medindo exatamente
estes numeros (`/tmp/tf-final/a2/calibracao.md`), e divergir da formula
invalidaria a calibracao.

Este modulo nunca olha o rotulo (`churn`) -- so compara distribuicao de
feature e de score. A leitura de "o que fazer" com esse numero fica fora
daqui: `cmd_baseline`/`cmd_drift` publicam metrica e gravam evidencia, ponto.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import serving
from .aws import log, put_metric_data
from .config import Config

# Faixa de leitura consagrada na indústria para PSI -- convenção que o
# mercado adotou, não uma lei estatística. Serve só para rotular o número no
# JSON; quem decide o que fazer com a leitura é quem lê o relatório.
_INTERPRETATION_BANDS = (
    (0.10, "estavel"),
    (0.25, "mudanca moderada"),
    (float("inf"), "mudanca relevante"),
)


def interpret(psi: float) -> str:
    for limit, label in _INTERPRETATION_BANDS:
        if psi < limit:
            return label
    return "mudanca relevante"


@dataclass(frozen=True)
class FeatureDrift:
    feature: str
    psi: float
    bins: int
    binning: str  # "quantil" (continua) ou "categoria" (poucos valores distintos)
    reference_share: list[float] = field(default_factory=list)
    observed_share: list[float] = field(default_factory=list)

    @property
    def interpretation(self) -> str:
        return interpret(self.psi)


def is_discrete(reference: np.ndarray, bins: int) -> bool:
    return int(np.unique(reference).size) <= bins


def _quantile_edges(reference: np.ndarray, bins: int) -> np.ndarray:
    """Bordas por quantil da referencia, com as pontas abertas para -inf/+inf
    -- um valor nunca visto na referencia precisa cair num bin, nao ser
    descartado."""
    quantiles = np.linspace(0.0, 1.0, bins + 1)
    edges = np.unique(np.quantile(reference, quantiles)).astype(float)
    if edges.size < 2:
        raise ValueError("referencia com um unico valor distinto: use o caminho categorico")
    edges[0] = -np.inf
    edges[-1] = np.inf
    return edges


def _shares_from_counts(counts: np.ndarray, epsilon: float, side: str) -> np.ndarray:
    total = counts.sum()
    if total == 0:
        raise ValueError(f"{side} esta vazia: nao ha como calcular PSI")
    shares = counts / total
    return np.where(shares == 0.0, epsilon, shares)


def _continuous_shares(
    reference: np.ndarray, observed: np.ndarray, bins: int, epsilon: float
) -> tuple[np.ndarray, np.ndarray]:
    edges = _quantile_edges(reference, bins)
    ref_counts, _ = np.histogram(reference, bins=edges)
    obs_counts, _ = np.histogram(observed, bins=edges)
    return (
        _shares_from_counts(ref_counts, epsilon, "a referencia"),
        _shares_from_counts(obs_counts, epsilon, "a janela observada"),
    )


def _categorical_shares(
    reference: np.ndarray, observed: np.ndarray, epsilon: float
) -> tuple[np.ndarray, np.ndarray]:
    categories = np.union1d(np.unique(reference), np.unique(observed))
    ref_counts = np.array([(reference == c).sum() for c in categories], dtype=float)
    obs_counts = np.array([(observed == c).sum() for c in categories], dtype=float)
    return (
        _shares_from_counts(ref_counts, epsilon, "a referencia"),
        _shares_from_counts(obs_counts, epsilon, "a janela observada"),
    )


def psi(
    reference: np.ndarray, observed: np.ndarray, bins: int, epsilon: float
) -> tuple[float, list[float], list[float], str]:
    """PSI entre duas amostras. Nao simetrico no caminho continuo (a regua
    vem da referencia); simetrico no caminho categorico (a regua e a uniao)."""
    reference = np.asarray(reference, dtype=float)
    observed = np.asarray(observed, dtype=float)

    if reference.size == 0:
        raise ValueError("a referencia esta vazia: nao ha como calcular bins")

    if is_discrete(reference, bins):
        binning = "categoria"
        ref_share, obs_share = _categorical_shares(reference, observed, epsilon)
    else:
        binning = "quantil"
        ref_share, obs_share = _continuous_shares(reference, observed, bins, epsilon)

    value = float(np.sum((obs_share - ref_share) * np.log(obs_share / ref_share)))
    return value, ref_share.tolist(), obs_share.tolist(), binning


def feature_drift(
    reference: dict[str, np.ndarray],
    observed: dict[str, np.ndarray],
    features: list[str],
    bins: int,
    epsilon: float,
) -> list[FeatureDrift]:
    results: list[FeatureDrift] = []
    for name in features:
        value, ref_share, obs_share, binning = psi(reference[name], observed[name], bins, epsilon)
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


# --------------------------------------------------------------------------- #
# Leitura de CSV e pontuacao do endpoint
# --------------------------------------------------------------------------- #


def _read_features(csv_path: Path, feature_order: list[str]) -> tuple[list[str], dict[str, np.ndarray]]:
    """Le um CSV com cabecalho e devolve (ids, {feature: array}). Aceita CSV
    com colunas extras (ex.: `churn` em reference.csv) -- so as colunas de
    `feature_order` sao usadas."""
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ValueError(f"{csv_path} nao tem linhas de dados")
    ids = [row.get("observation_id", "") for row in rows]
    features = {name: np.array([float(row[name]) for row in rows], dtype=float) for name in feature_order}
    return ids, features


def _score(cfg: Config, csv_path: Path) -> np.ndarray:
    """Probabilidade de churn de cada linha do CSV, via endpoint Real-Time --
    o candidato sempre presente (as tres arquiteturas de serving sao
    comparadas em `make compare`; Real-Time e a que este modulo usa para
    pontuar trafego de producao). Lotes de 100 linhas por chamada, dentro do
    limite pratico de payload do InvokeEndpoint."""
    outputs = serving.terraform_outputs(cfg)
    endpoint_name = serving.require_output(outputs, "realtime_endpoint_name")
    _, rows = serving.build_payload_rows(csv_path, cfg)

    probabilities: list[float] = []
    batch_size = 100
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        body = "\n".join(chunk) + "\n"
        result = serving.invoke_realtime(endpoint_name, body, cfg.region)
        probabilities.extend(result.probabilities)
    return np.array(probabilities, dtype=float)


def _drift_config(cfg: Config) -> tuple[int, float, float]:
    """bins/epsilon vem de `acceptance.yaml` (a calibracao real de A2 usou
    bins=5, nao o default de 10 do lab 04.1); o limiar de alerta por feature
    e `cfg.psi_threshold`, congelado em 0.20."""
    drift_cfg = cfg.acceptance.get("drift", {})
    bins = int(drift_cfg.get("bins", 10))
    epsilon = float(drift_cfg.get("epsilon", 1e-6))
    return bins, epsilon, cfg.psi_threshold


def _evidence_payload(
    window: str,
    drifts: list[FeatureDrift],
    prediction_psi: float,
    predicted_churn_rate: float,
    threshold: float,
    notes: list[str],
) -> dict[str, Any]:
    """Schema conciliado com o que A7 assumiu em PEDIDOS.md (`data_drift_psi_max`,
    `feature_with_max_psi`, `features_above_threshold`, `prediction_drift_psi`,
    `predicted_churn_rate`, `notes`) mais `feature_psi`/`feature_detail`, que a
    tarefa deste agente exige e o schema de A7 nao tinha (registrado em
    PEDIDOS.md para A7 ajustar o `evidence.py` se precisar)."""
    ordered = sorted(drifts, key=lambda d: d.psi, reverse=True)
    above = [d.feature for d in ordered if d.psi >= threshold]
    return {
        "window": window,
        "data_drift_psi_max": round(ordered[0].psi, 6) if ordered else 0.0,
        "feature_with_max_psi": ordered[0].feature if ordered else None,
        "features_above_threshold": above,
        "feature_psi": {d.feature: round(d.psi, 6) for d in drifts},
        "feature_detail": [
            {
                "feature": d.feature,
                "psi": round(d.psi, 6),
                "binning": d.binning,
                "interpretation": d.interpretation,
            }
            for d in ordered
        ],
        "prediction_drift_psi": round(prediction_psi, 6),
        "predicted_churn_rate": round(predicted_churn_rate, 6),
        "psi_threshold": threshold,
        "notes": notes,
    }


def _run_window(cfg: Config, window: str, observed_csv: Path, evidence_name: str) -> dict[str, Any]:
    reference_csv = cfg.data_dir / "reference.csv"
    bins, epsilon, threshold = _drift_config(cfg)

    _, reference_features = _read_features(reference_csv, cfg.feature_order)
    _, observed_features = _read_features(observed_csv, cfg.feature_order)

    drifts = feature_drift(reference_features, observed_features, cfg.feature_order, bins, epsilon)
    for d in drifts:
        log(f"[drift] {window} {d.feature}: psi={d.psi:.6f} ({d.binning}, {d.interpretation})")

    reference_scores = _score(cfg, reference_csv)
    observed_scores = _score(cfg, observed_csv)
    prediction_psi_value, _, _, _ = psi(reference_scores, observed_scores, bins, epsilon)
    predicted_churn_rate = float((observed_scores >= 0.5).mean())

    payload = _evidence_payload(
        window,
        drifts,
        prediction_psi_value,
        predicted_churn_rate,
        threshold,
        notes=[],
    )

    # Dimensoes fixadas por A6a em PEDIDOS.md (`terraform/monitoring.tf` e
    # `terraform/dashboard.tf` ja aplicados quando este modulo foi escrito):
    # os dois alarmes leem a metrica SEM NENHUMA dimensao -- os tres
    # endpoints (Real-Time/Serverless/Async) coexistem e qual deles esta
    # "em producao" e escolha do aluno em `student/solution.yaml`, nunca uma
    # dimensao fixada em Terraform. O dashboard, por sua vez, separa por
    # `Window` (valores exatos "baseline"/"shifted"). Por isso cada metrica
    # de janela sai duas vezes: uma sem dimensao (o que o alarme avalia) e
    # uma com `Window` (o que o dashboard mostra) -- nunca com `EndpointName`,
    # que criaria uma serie que nem alarme nem dashboard leem.
    window_dims = {"Window": window}

    max_psi = payload["data_drift_psi_max"]
    put_metric_data(cfg.namespace, "DataDriftPSIMax", max_psi, cfg.region)
    put_metric_data(cfg.namespace, "DataDriftPSIMax", max_psi, cfg.region, dimensions=window_dims)
    put_metric_data(cfg.namespace, "PredictionDriftPSI", prediction_psi_value, cfg.region)
    put_metric_data(
        cfg.namespace, "PredictionDriftPSI", prediction_psi_value, cfg.region, dimensions=window_dims
    )
    put_metric_data(
        cfg.namespace, "PredictedChurnRate", predicted_churn_rate, cfg.region, dimensions=window_dims
    )
    log(f"[drift] {window}: metricas publicadas em {cfg.namespace}")

    evidence_path = cfg.evidence_dir / evidence_name
    evidence_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    log(f"[drift] evidencia em {evidence_path}")

    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return payload


def cmd_baseline(cfg: Config, args: Any) -> int:
    """Janela saudavel: production_baseline.csv contra reference.csv."""
    _run_window(cfg, "baseline", cfg.data_dir / "production_baseline.csv", "baseline-drift.json")
    return 0


def cmd_drift(cfg: Config, args: Any) -> int:
    """Janela apos a mudanca comercial: production_shifted.csv contra reference.csv.

    Nao publica `ReactionTriggered` -- essa metrica e da Lambda de reacao
    (`lambda/drift_response.py`, A6a), que a publica sem dimensao quando de
    fato reage a um alarme em ALARM (ver PEDIDOS.md, entrada `a6a`). Publicar
    aqui de novo, so porque o limiar foi cruzado no dado, duplicaria o nome
    da metrica com um significado diferente do que o alarme/Lambda emitem.
    """
    _run_window(cfg, "shifted", cfg.data_dir / "production_shifted.csv", "production-drift.json")
    return 0
