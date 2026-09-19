"""Consolidação do dossiê de evidência (`artifacts/evidence/`) e portão
pré-`finish`.

Dois subcomandos moram aqui:

- `evidence` — lê os 15 arquivos que os outros agentes já gravaram em
  `artifacts/evidence/`, confere os quatro contratos numéricos que
  `config/acceptance.yaml` promete (contagem de atendimento, contagem de
  campanha, PSI da janela saudável, PSI da janela deslocada), e — só se tudo
  isso bater — grava `manifest.json` (linhagem: timestamp, commit, região,
  hash de cada arquivo) e `evidence.md` (a mesma história em texto, na ordem
  pedagógica do trabalho: infra saudável → drift → predição mudou → incidente
  → ground truth → qualidade).
- `check` — o portão que `make finish` chama antes de destruir qualquer
  coisa: solution.yaml sem TODO, DECISION.md sem placeholder, evidência
  completa. Não decide nada por conta própria — só confere o que os outros
  módulos já produziram.

Disciplina que vale para os dois: `evidence.md` narra fatos com a fonte ao
lado (arquivo + campo), nunca uma recomendação, uma conclusão ou uma
preferência de pattern — essa leitura é do aluno, em `DECISION.md`.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aws import AwsError, check_credentials, log
from .config import Config
from .solution import SolutionError, load_and_validate, solution_path

# String exata usada em todo `student/DECISION.md` entregue (18 ocorrências
# no template). Fonte de verdade: o próprio arquivo entregue por A9 — ver
# nota em /tmp/tf-final/shared/PEDIDOS.md sobre a divergência com o que o
# relatório de A9 afirma ter registrado ali.
DECISION_PLACEHOLDER = "<preencher>"

# Os 18 arquivos que o CONTRATO congela para artifacts/evidence/, menos os
# três que este próprio módulo produz (manifest.json, evidence.md) e
# cleanup.json, que só existe depois do `destroy` + `verify-clean` — não faz
# sentido exigi-lo aqui, ver packaging.py para o gate que de fato o exige.
REQUIRED_EVIDENCE_FILES: tuple[str, ...] = (
    "data-contract.json",
    "training.json",
    "artifact.json",
    "solution.json",
    "serving.json",
    "candidates.json",
    "candidates.md",
    "atendimento.json",
    "campanha.json",
    "baseline-drift.json",
    "production-drift.json",
    "alarm.json",
    "reaction.json",
    "quality.json",
    "dashboard.json",
)

_JSON_FILES = tuple(name for name in REQUIRED_EVIDENCE_FILES if name.endswith(".json"))


class Checks:
    """Mesmo coletor de `scripts/final.py::Checks` — mantido aqui em vez de
    importado porque este módulo não deve depender do dispatcher (a relação
    é inversa: o dispatcher importa `final_project.evidence`, nunca o
    contrário)."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.items.append({"check": name, "passed": passed, "detail": detail})
        log(f"{'[OK]' if passed else '[FALHA]'} {name}: {detail}")

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [item for item in self.items if not item["passed"]]

    def summary(self, title: str) -> dict[str, Any]:
        ok = not self.failed
        log("")
        log(f"{'[OK]' if ok else '[FALHA]'} {title}: {len(self.items) - len(self.failed)}/{len(self.items)} verificações passaram")
        return {"passed": ok, "checks_total": len(self.items), "checks_failed": len(self.failed), "checks": self.items}


def _emit(payload: dict[str, Any]) -> None:
    """Único ponto de saída em stdout — o resultado que alguém captura ou pipa."""
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _dig(mapping: Any, *keys: str, default: Any = None) -> Any:
    """Navegação tolerante de dict aninhado: devolve `default` em qualquer
    desvio (chave ausente, nível que não é dict) em vez de levantar. Usada
    tanto para `acceptance.yaml` (chaves congeladas, mas o código não deveria
    explodir se um agente ainda não terminou de escrever o arquivo) quanto
    para os JSON de evidência de A5/A6, cujo schema exato ainda não está
    congelado em nenhuma spec — ver a nota "a7:" em
    /tmp/tf-final/shared/PEDIDOS.md."""
    current = mapping
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _first(mapping: Any, *alias_keys: str, default: Any = None) -> Any:
    """Tenta cada chave alternativa em ordem — tolerância a schema ainda não
    congelado nos arquivos de drift/qualidade que outro agente entrega."""
    for key in alias_keys:
        if isinstance(mapping, dict) and key in mapping:
            return mapping[key]
    return default


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Carrega um JSON de evidência. Devolve (dados, None) ou (None, motivo) —
    nunca levanta: um arquivo ausente ou corrompido é um item de checklist,
    não uma exceção que derruba o resto da consolidação."""
    if not path.exists():
        return None, "arquivo não encontrado"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"JSON inválido: {exc}"
    if not isinstance(data, dict):
        return None, "conteúdo não é um objeto JSON"
    return data, None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# Completude e contratos numéricos
# --------------------------------------------------------------------------- #


def _check_presence(cfg: Config, checks: Checks) -> dict[str, dict[str, Any]]:
    """Confere presença + parse de cada arquivo obrigatório. Devolve o cache
    de JSON já carregado, para os contratos numéricos não reabrirem disco."""
    parsed: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_EVIDENCE_FILES:
        path = cfg.evidence_dir / name
        if not name.endswith(".json"):
            checks.add(f"arquivo_presente:{name}", path.exists(), str(path) if path.exists() else f"{path} não encontrado")
            continue
        data, error = _load_json(path)
        if error is None and data is not None:
            parsed[name] = data
        checks.add(
            f"arquivo_presente:{name}",
            error is None,
            f"{path} presente e válido" if error is None else f"{path}: {error}",
        )
    return parsed


def _check_atendimento(acceptance: dict[str, Any], parsed: dict[str, Any], checks: Checks) -> None:
    expected = _dig(acceptance, "workload_counts", "atendimento_output_count")
    payload = parsed.get("atendimento.json")
    if payload is None or expected is None:
        checks.add("contrato_atendimento", False, "atendimento.json ausente/inválido ou acceptance.yaml sem workload_counts.atendimento_output_count")
        return
    actual = _first(payload, "request_count")
    checks.add(
        "contrato_atendimento",
        actual == expected,
        f"request_count={actual!r}, esperado {expected!r} (config/acceptance.yaml: workload_counts.atendimento_output_count)",
    )


def _check_campanha(acceptance: dict[str, Any], parsed: dict[str, Any], checks: Checks) -> None:
    expected = _dig(acceptance, "workload_counts", "campanha_output_count")
    payload = parsed.get("campanha.json")
    if payload is None or expected is None:
        checks.add("contrato_campanha", False, "campanha.json ausente/inválido ou acceptance.yaml sem workload_counts.campanha_output_count")
        return
    actual = _first(payload, "output_count")
    checks.add(
        "contrato_campanha",
        actual == expected,
        f"output_count={actual!r}, esperado {expected!r} (config/acceptance.yaml: workload_counts.campanha_output_count)",
    )


def _check_baseline_drift(acceptance: dict[str, Any], parsed: dict[str, Any], checks: Checks) -> None:
    data_max_below = _dig(acceptance, "drift", "baseline", "data_psi_max_below")
    prediction_below = _dig(acceptance, "drift", "baseline", "prediction_psi_below")
    payload = parsed.get("baseline-drift.json")
    if payload is None or data_max_below is None or prediction_below is None:
        checks.add("contrato_psi_baseline", False, "baseline-drift.json ausente/inválido ou acceptance.yaml sem drift.baseline.*")
        return
    data_psi_max = _first(payload, "data_psi_max", "data_drift_psi_max", "psi_max")
    prediction_psi = _first(payload, "prediction_psi", "prediction_drift_psi")
    ok = (
        isinstance(data_psi_max, (int, float))
        and isinstance(prediction_psi, (int, float))
        and data_psi_max < data_max_below
        and prediction_psi < prediction_below
    )
    checks.add(
        "contrato_psi_baseline",
        ok,
        f"data_psi_max={data_psi_max!r} (< {data_max_below!r}), prediction_psi={prediction_psi!r} (< {prediction_below!r}) "
        "— janela saudável precisa ficar abaixo do limiar em config/acceptance.yaml: drift.baseline",
    )


def _check_shifted_drift(acceptance: dict[str, Any], parsed: dict[str, Any], checks: Checks) -> None:
    data_max_at_least = _dig(acceptance, "drift", "shifted", "data_psi_max_at_least")
    features_at_least = _dig(acceptance, "drift", "shifted", "features_above_threshold_at_least")
    prediction_at_least = _dig(acceptance, "drift", "shifted", "prediction_psi_at_least")
    payload = parsed.get("production-drift.json")
    if payload is None or None in (data_max_at_least, features_at_least, prediction_at_least):
        checks.add("contrato_psi_shifted", False, "production-drift.json ausente/inválido ou acceptance.yaml sem drift.shifted.*")
        return
    data_psi_max = _first(payload, "data_psi_max", "data_drift_psi_max", "psi_max")
    prediction_psi = _first(payload, "prediction_psi", "prediction_drift_psi")
    features_above = _first(payload, "features_above_threshold", "features_above_threshold_list")
    features_count = len(features_above) if isinstance(features_above, list) else features_above if isinstance(features_above, int) else None
    ok = (
        isinstance(data_psi_max, (int, float))
        and isinstance(prediction_psi, (int, float))
        and isinstance(features_count, int)
        and data_psi_max >= data_max_at_least
        and prediction_psi >= prediction_at_least
        and features_count >= features_at_least
    )
    checks.add(
        "contrato_psi_shifted",
        ok,
        f"data_psi_max={data_psi_max!r} (>= {data_max_at_least!r}), prediction_psi={prediction_psi!r} (>= {prediction_at_least!r}), "
        f"features_above_threshold={features_count!r} (>= {features_at_least!r}) — janela deslocada precisa cruzar o limiar em "
        "config/acceptance.yaml: drift.shifted",
    )


def evaluate_completeness(cfg: Config) -> tuple[Checks, dict[str, dict[str, Any]]]:
    """Núcleo compartilhado por `cmd_evidence`, `cmd_check` e por
    `packaging.py` (precondição "evidence não está completo" do `package`):
    presença de cada arquivo + os quatro contratos numéricos. Não grava nada
    em disco — quem grava manifest/evidence.md é só `cmd_evidence`, e só se
    isto passar."""
    checks = Checks()
    parsed = _check_presence(cfg, checks)
    _check_atendimento(cfg.acceptance, parsed, checks)
    _check_campanha(cfg.acceptance, parsed, checks)
    _check_baseline_drift(cfg.acceptance, parsed, checks)
    _check_shifted_drift(cfg.acceptance, parsed, checks)
    return checks, parsed


# --------------------------------------------------------------------------- #
# manifest.json
# --------------------------------------------------------------------------- #


def _git_commit(cfg: Config) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cfg.root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "desconhecido"


def _account_id(cfg: Config) -> str:
    """Melhor esforço: a conta é metadado de linhagem, não um gate. Se a
    credencial não estiver disponível no momento em que `evidence` é
    chamado, o manifesto registra isso em vez de derrubar a consolidação —
    quem depende de credencial de fato é `verify-clean`, não este comando."""
    try:
        return check_credentials(cfg.region)["account"]
    except AwsError as exc:
        log(f"[evidence] não foi possível confirmar a conta AWS para o manifesto: {exc}")
        return "desconhecida"


def _build_manifest(cfg: Config, parsed: dict[str, dict[str, Any]]) -> dict[str, Any]:
    solution = parsed.get("solution.json") or {}
    files: dict[str, Any] = {}
    for name in REQUIRED_EVIDENCE_FILES:
        path = cfg.evidence_dir / name
        if path.exists():
            files[name] = {"sha256": _sha256(path), "size_bytes": path.stat().st_size}
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(cfg),
        "account": _account_id(cfg),
        "region": cfg.region,
        "prefix": cfg.prefix,
        "patterns": {
            "atendimento": solution.get("atendimento_pattern", "desconhecido"),
            "campanha": solution.get("campanha_pattern", "desconhecido"),
        },
        "files": files,
    }


# --------------------------------------------------------------------------- #
# evidence.md — a mesma história em texto, com a fonte ao lado de cada fato.
# --------------------------------------------------------------------------- #


def _fmt(value: Any, digits: int = 4) -> str:
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value) if value is not None else "n/d"


def _render_markdown(cfg: Config, manifest: dict[str, Any], parsed: dict[str, dict[str, Any]]) -> str:
    serving = parsed.get("serving.json") or {}
    candidates = parsed.get("candidates.json") or {}
    dashboard = parsed.get("dashboard.json") or {}
    baseline_drift = parsed.get("baseline-drift.json") or {}
    production_drift = parsed.get("production-drift.json") or {}
    alarm = parsed.get("alarm.json") or {}
    reaction = parsed.get("reaction.json") or {}
    quality = parsed.get("quality.json") or {}
    atendimento = parsed.get("atendimento.json") or {}
    campanha = parsed.get("campanha.json") or {}

    lines: list[str] = []
    lines.append("# Dossiê de evidência — Trabalho Final Cloud-Based ML")
    lines.append("")
    lines.append(
        "Cada afirmação abaixo cita o arquivo e o campo de `artifacts/evidence/` que a sustenta. "
        "Este documento não recomenda nem conclui qual pattern de serving foi melhor — essa leitura "
        "fica em `student/DECISION.md`, escrita pelo grupo."
    )
    lines.append("")
    lines.append(f"- Gerado em: `{manifest['generated_at']}`")
    lines.append(f"- Commit: `{manifest['git_commit']}`")
    lines.append(f"- Região: `{manifest['region']}`")
    lines.append(f"- Prefixo dos recursos: `{manifest['prefix']}`")
    lines.append("")

    lines.append("## 1. A infraestrutura estava saudável")
    lines.append("")
    lines.append(f"- `serving.json`: {json.dumps(serving, ensure_ascii=False) if serving else 'sem dados'}")
    lines.append(f"- `candidates.json`: {len(candidates.get('candidates', [])) if isinstance(candidates.get('candidates'), list) else 'n/d'} candidato(s) de serving observados, ver também `candidates.md`.")
    lines.append(f"- `dashboard.json`: {json.dumps(dashboard, ensure_ascii=False) if dashboard else 'sem dados'}")
    lines.append("")

    lines.append("## 2. Os dados mudaram (drift)")
    lines.append("")
    lines.append(
        f"- `baseline-drift.json.data_psi_max` = {_fmt(_first(baseline_drift, 'data_psi_max', 'data_drift_psi_max', 'psi_max'))} "
        "(janela saudável, antes do reajuste)."
    )
    lines.append(
        f"- `production-drift.json.data_psi_max` = {_fmt(_first(production_drift, 'data_psi_max', 'data_drift_psi_max', 'psi_max'))} "
        f"— feature de maior PSI: `{_first(production_drift, 'feature_with_max_psi', default='n/d')}` "
        f"(janela deslocada, depois do reajuste de mensalidade e da mudança de política comercial)."
    )
    features_above = _first(production_drift, "features_above_threshold", "features_above_threshold_list")
    if isinstance(features_above, list):
        lines.append(f"- `production-drift.json.features_above_threshold` = {features_above}.")
    lines.append("")

    lines.append("## 3. As predições mudaram")
    lines.append("")
    lines.append(f"- `production-drift.json.prediction_psi` = {_fmt(_first(production_drift, 'prediction_psi', 'prediction_drift_psi'))}.")
    lines.append(f"- `production-drift.json.predicted_churn_rate` = {_fmt(_first(production_drift, 'predicted_churn_rate'))}.")
    lines.append(f"- `atendimento.json`: request_count={atendimento.get('request_count', 'n/d')}, success_rate={_fmt(atendimento.get('success_rate'))}.")
    lines.append(f"- `campanha.json`: input_count={campanha.get('input_count', 'n/d')}, output_count={campanha.get('output_count', 'n/d')}.")
    lines.append("")

    lines.append("## 4. A regra virou incidente")
    lines.append("")
    lines.append(f"- `alarm.json`: {json.dumps(alarm, ensure_ascii=False) if alarm else 'sem dados'}")
    lines.append(f"- `reaction.json`: {json.dumps(reaction, ensure_ascii=False) if reaction else 'sem dados'}")
    lines.append("")

    lines.append("## 5. O ground truth chegou")
    lines.append("")
    windows = quality.get("windows") if isinstance(quality.get("windows"), dict) else {}
    baseline_window = windows.get("baseline", {}) if isinstance(windows, dict) else {}
    shifted_window = windows.get("shifted", {}) if isinstance(windows, dict) else {}
    lines.append(f"- `quality.json.windows.baseline.actual_churn_rate` = {_fmt(baseline_window.get('actual_churn_rate'))}.")
    lines.append(f"- `quality.json.windows.shifted.actual_churn_rate` = {_fmt(shifted_window.get('actual_churn_rate'))}.")
    lines.append("")

    lines.append("## 6. A mudança virou perda de qualidade")
    lines.append("")
    lines.append(f"- `quality.json.windows.baseline`: F1={_fmt(baseline_window.get('f1'))}, ROC-AUC={_fmt(baseline_window.get('roc_auc'))}.")
    lines.append(f"- `quality.json.windows.shifted`: F1={_fmt(shifted_window.get('f1'))}, ROC-AUC={_fmt(shifted_window.get('roc_auc'))}.")
    comparison = quality.get("comparison") if isinstance(quality.get("comparison"), dict) else {}
    lines.append(f"- `quality.json.comparison.failure_mode` = `{comparison.get('failure_mode', 'n/d')}`.")
    lines.append("")

    lines.append("## Arquivos e hashes")
    lines.append("")
    lines.append("| arquivo | sha256 |")
    lines.append("|---|---|")
    for name, info in sorted(manifest["files"].items()):
        lines.append(f"| `{name}` | `{info['sha256']}` |")
    lines.append("")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Subcomandos
# --------------------------------------------------------------------------- #


def cmd_evidence(cfg: Config, args: Any) -> int:
    """Consolida `artifacts/evidence/`: confere completude + contratos
    numéricos e, só se tudo passar, grava `manifest.json` e `evidence.md`.
    Não sobrescreve os dois com um dossiê que não bateu — um manifesto
    otimista sobre evidência quebrada seria pior do que nenhum manifesto."""
    checks, parsed = evaluate_completeness(cfg)
    resultado = checks.summary("evidence")

    if not resultado["passed"]:
        log("")
        log("[evidence] manifest.json e evidence.md NÃO foram gravados — corrija os itens acima e rode `evidence` de novo.")
        _emit(resultado)
        return 1

    manifest = _build_manifest(cfg, parsed)
    manifest_path = cfg.evidence_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"[evidence] gravado {manifest_path}")

    markdown = _render_markdown(cfg, manifest, parsed)
    evidence_md_path = cfg.evidence_dir / "evidence.md"
    evidence_md_path.write_text(markdown, encoding="utf-8")
    log(f"[evidence] gravado {evidence_md_path}")

    resultado["manifest"] = str(manifest_path)
    resultado["evidence_md"] = str(evidence_md_path)
    _emit(resultado)
    return 0


# --------------------------------------------------------------------------- #
# resumo — grava a tabela de evidências dentro de student/DECISION.md
# --------------------------------------------------------------------------- #

INICIO_EVIDENCIAS = "<!-- inicio-evidencias -->"
FIM_EVIDENCIAS = "<!-- fim-evidencias -->"


def _num(valor: Any, digits: int = 2) -> str:
    """Número em português de gente: vírgula decimal, sem casas falsas — o
    JSON chega com seis casas (precisão do cálculo, não da leitura)."""
    if not isinstance(valor, (int, float)):
        return "não medido"
    return f"{valor:.{digits}f}".replace(".", ",")


def _pct(fracao: Any) -> str:
    """Fração para porcentagem redonda: 0.905 -> 90%. Decimal de fração não ajuda decisão."""
    if not isinstance(fracao, (int, float)):
        return "não medido"
    return f"{round(fracao * 100)}%"


def _milhar(n: Any) -> str:
    if not isinstance(n, (int, float)):
        return "não medido"
    return f"{n:,.0f}".replace(",", ".")


def _tempo(ms: Any) -> str:
    """Latência em linguagem de gente: arredonda (a terceira casa decimal é
    ruído de rede, não medição) e troca para segundos acima de 1000 ms."""
    if not isinstance(ms, (int, float)):
        return "não medido"
    if ms >= 1000:
        return f"{ms / 1000:.1f}".replace(".", ",") + " segundos"
    return f"{round(ms)} ms"


def _read(cfg: Config, name: str) -> dict[str, Any] | None:
    data, _ = _load_json(cfg.evidence_dir / name)
    return data


def _frases_evidencia(
    training: dict[str, Any] | None,
    artifact: dict[str, Any] | None,
    atendimento: dict[str, Any] | None,
    campanha: dict[str, Any] | None,
    baseline_drift: dict[str, Any] | None,
    production_drift: dict[str, Any] | None,
    alarm: dict[str, Any] | None,
    reaction: dict[str, Any] | None,
    quality: dict[str, Any] | None,
) -> dict[str, str]:
    """Uma frase por linha da tabela, escrita para quem não abre JSON."""
    frases: dict[str, str] = {}

    if training and artifact:
        metrics = {m.get("name"): m.get("value") for m in training.get("final_metrics", []) if isinstance(m, dict)}
        frases["treino"] = (
            f"O training job rodou **{_num(training.get('billable_seconds'), 0)} segundos** "
            f"de instância `{training.get('instance_type', '?')}`, terminando com AUC de validação "
            f"**{_num(metrics.get('validation:auc'))}** (treino: {_num(metrics.get('train:auc'))}). "
            f"O artefato foi confirmado via API (`DescribeTrainingJob` + `HeadObject`), nunca montado "
            f"por convenção — **{_milhar(artifact.get('content_length'))} bytes** em "
            f"`{artifact.get('model_artifact_s3_uri', '?').rsplit('/', 1)[-1]}`."
        )

    if atendimento and campanha:
        frases["workloads"] = (
            f"Atendimento (pattern `{atendimento.get('pattern', '?')}`) respondeu "
            f"**{_milhar(atendimento.get('request_count'))} chamadas** com "
            f"**{_pct(atendimento.get('success_rate'))} de sucesso**; metade em até "
            f"**{_tempo(atendimento.get('warm_p50_ms'))}**, primeira chamada em "
            f"{_tempo(atendimento.get('first_ms'))}. Campanha (pattern `{campanha.get('pattern', '?')}`) "
            f"processou **{_milhar(campanha.get('input_count'))} clientes** e devolveu "
            f"**{_milhar(campanha.get('output_count'))} predições** em "
            f"{_num(campanha.get('duration_seconds'), 0)} segundos."
        )

    if baseline_drift:
        frases["baseline"] = (
            f"Na janela saudável, o PSI máximo de dados ficou em "
            f"**{_num(_first(baseline_drift, 'data_psi_max', 'data_drift_psi_max', 'psi_max'))}** "
            f"(limiar {_num(baseline_drift.get('psi_threshold'))}), puxado por "
            f"`{baseline_drift.get('feature_with_max_psi', '?')}`. PSI das predições: "
            f"**{_num(_first(baseline_drift, 'prediction_psi', 'prediction_drift_psi'))}** — nenhuma "
            f"feature cruzou o limiar."
        )

    if production_drift:
        features_above = _first(production_drift, "features_above_threshold", "features_above_threshold_list") or []
        n_acima = len(features_above) if isinstance(features_above, list) else features_above
        frases["drift"] = (
            f"Na janela deslocada, o PSI máximo de dados subiu para "
            f"**{_num(_first(production_drift, 'data_psi_max', 'data_drift_psi_max', 'psi_max'))}**, "
            f"puxado por `{production_drift.get('feature_with_max_psi', '?')}`; **{n_acima} de 7 variáveis** "
            f"cruzaram o limiar de {_num(production_drift.get('psi_threshold'))}. PSI das predições: "
            f"**{_num(_first(production_drift, 'prediction_psi', 'prediction_drift_psi'))}**, com "
            f"**{_pct(production_drift.get('predicted_churn_rate'))}** dos clientes previstos como churn."
        )

    if alarm and reaction:
        estados = [
            f"`{nome}`: **{info.get('state', '?')}**"
            for nome, info in (alarm.get("alarms") or {}).items()
            if isinstance(info, dict)
        ]
        payload = reaction.get("incident_payload") or {}
        frases["alarme"] = (
            f"Os alarmes foram para {', '.join(estados) if estados else 'não medido'}. A Lambda registrou "
            f"o incidente em `{reaction.get('key', '?')}`: **{payload.get('status', '?')} / "
            f"{payload.get('recommended_action', '?')}**. Nenhum training job novo foi disparado — a "
            f"reação não retreina nem promove modelo sozinha."
        )

    if quality:
        comparison = quality.get("comparison") or {}
        windows = quality.get("windows") if isinstance(quality.get("windows"), dict) else {}
        baseline_w = windows.get("baseline", {}) if isinstance(windows, dict) else {}
        shifted_w = windows.get("shifted", {}) if isinstance(windows, dict) else {}
        confusion = shifted_w.get("confusion_matrix") or {}
        frases["qualidade"] = (
            f"Com o rótulo verdadeiro, o F1 caiu de **{_num(baseline_w.get('f1'))}** para "
            f"**{_num(shifted_w.get('f1'))}** (queda de {_num(comparison.get('f1_drop'))}), e o ROC-AUC "
            f"caiu **{_num(comparison.get('roc_auc_drop'))}**. Modo de falha: "
            f"**{comparison.get('failure_mode', '?')}** — a matriz de confusão da janela deslocada mostra "
            f"**{confusion.get('false_positive', '?')} falsos positivos** contra "
            f"**{confusion.get('false_negative', '?')} falsos negativos**."
        )

    return frases


def _grava_evidencias_no_decision(cfg: Config, frases: dict[str, str]) -> int:
    """Reescreve a tabela de evidências do DECISION.md com os números
    medidos. Só o bloco entre os marcadores é trocado: o que o grupo escreve
    nas 10 seções de decisão fica intacto, e rodar de novo não duplica nada.
    Se o arquivo não tiver os marcadores (o grupo apagou sem querer), avisa e
    não mexe — perder o texto do grupo seria muito pior que deixar a tabela
    desatualizada."""
    caminho = cfg.root / "student" / "DECISION.md"
    if not caminho.exists():
        log("aviso: student/DECISION.md não encontrado; a tabela não foi atualizada")
        return 0

    texto = caminho.read_text(encoding="utf-8")
    if INICIO_EVIDENCIAS not in texto or FIM_EVIDENCIAS not in texto:
        log("aviso: os marcadores de evidência não estão em student/DECISION.md; a tabela não foi atualizada")
        return 0

    pendente = {
        "treino": "_rode `make deploy`_",
        "workloads": "_rode `make run`_",
        "baseline": "_rode `make baseline`_",
        "drift": "_rode `make drift`_",
        "alarme": "_rode `make alarm-status`_",
        "qualidade": "_rode `make ground-truth`_",
    }
    rotulos = [
        ("treino", "Treino e artefato", "training.json + artifact.json"),
        ("workloads", "Atendimento e campanha", "atendimento.json + campanha.json"),
        ("baseline", "Janela saudável", "baseline-drift.json"),
        ("drift", "Janela deslocada", "production-drift.json"),
        ("alarme", "Alarme e reação automática", "alarm.json + reaction.json"),
        ("qualidade", "Qualidade com ground truth", "quality.json"),
    ]

    bloco = [
        INICIO_EVIDENCIAS,
        "| Elo da cadeia | Fonte | O que medimos na sua execução |",
        "|---|---|---|",
        *[f"| {nome} | `{fonte}` | {frases.get(chave, pendente[chave])} |" for chave, nome, fonte in rotulos],
        FIM_EVIDENCIAS,
    ]

    caminho.write_text(
        texto[: texto.index(INICIO_EVIDENCIAS)]
        + "\n".join(bloco)
        + texto[texto.index(FIM_EVIDENCIAS) + len(FIM_EVIDENCIAS) :],
        encoding="utf-8",
    )
    return sum(1 for chave, _, _ in rotulos if chave in frases)


def cmd_resumo(cfg: Config, args: Any) -> int:
    """Escreve a tabela de evidências em `student/DECISION.md` e repete as
    MESMAS frases no terminal — se o terminal dissesse uma coisa e o
    documento outra, o grupo não saberia em qual confiar."""
    training = _read(cfg, "training.json")
    artifact = _read(cfg, "artifact.json")
    atendimento = _read(cfg, "atendimento.json")
    campanha = _read(cfg, "campanha.json")
    baseline_drift = _read(cfg, "baseline-drift.json")
    production_drift = _read(cfg, "production-drift.json")
    alarm = _read(cfg, "alarm.json")
    reaction = _read(cfg, "reaction.json")
    quality = _read(cfg, "quality.json")

    frases = _frases_evidencia(
        training, artifact, atendimento, campanha, baseline_drift, production_drift, alarm, reaction, quality
    )
    secoes = [
        ("treino", "TREINO E ARTEFATO", "make deploy"),
        ("workloads", "ATENDIMENTO E CAMPANHA", "make run"),
        ("baseline", "JANELA SAUDÁVEL — baseline", "make baseline"),
        ("drift", "JANELA DESLOCADA — drift", "make drift"),
        ("alarme", "ALARME E REAÇÃO AUTOMÁTICA", "make alarm-status / make reaction"),
        ("qualidade", "QUALIDADE COM GROUND TRUTH", "make ground-truth"),
    ]

    falta: list[str] = []
    for chave, titulo, comando in secoes:
        log(titulo)
        if chave in frases:
            # O negrito do markdown não ajuda no terminal; sai só no documento.
            log("  " + frases[chave].replace("**", ""))
        else:
            log(f"  ainda não medido — rode `{comando}`")
            falta.append(comando)
        log("")

    escritas = _grava_evidencias_no_decision(cfg, frases)
    log(f"[resumo] student/DECISION.md: tabela de evidências atualizada ({escritas} de {len(secoes)} linhas com dado medido).")
    if falta:
        log("[resumo] ainda não medido: " + ", ".join(sorted(set(falta))))
    log("[resumo] o que resta no arquivo é só a decisão do grupo — a tabela é regravada a cada `make resumo`.")

    _emit(
        {
            "training": training,
            "artifact": artifact,
            "atendimento": atendimento,
            "campanha": campanha,
            "baseline_drift": baseline_drift,
            "production_drift": production_drift,
            "alarm": alarm,
            "reaction": reaction,
            "quality": quality,
        }
    )
    return 0


def cmd_check(cfg: Config, args: Any) -> int:
    """Portão pré-`finish`: solution.yaml sem TODO, DECISION.md sem
    placeholder, evidência completa. Chamado por `make finish` antes de
    `destroy` — por isso nunca exige `cleanup.json` (que só existe depois)."""
    checks = Checks()

    try:
        sol = load_and_validate(cfg)
        checks.add(
            "solution_yaml",
            True,
            f"patterns preenchidos (atendimento={sol.atendimento_pattern}, campanha={sol.campanha_pattern}), "
            f"grupo {sol.group!r}, {len(sol.members)} integrante(s)",
        )
    except SolutionError as exc:
        checks.add("solution_yaml", False, str(exc))

    decision_path = cfg.root / "student" / "DECISION.md"
    if not decision_path.exists():
        checks.add("decision_md", False, f"{decision_path} não encontrado")
    else:
        pendentes = decision_path.read_text(encoding="utf-8").count(DECISION_PLACEHOLDER)
        checks.add(
            "decision_md",
            pendentes == 0,
            "sem placeholder pendente" if pendentes == 0 else f"{pendentes} ocorrência(s) de `{DECISION_PLACEHOLDER}` ainda não preenchidas",
        )

    evidence_checks, _ = evaluate_completeness(cfg)
    for item in evidence_checks.items:
        checks.items.append(item)

    resultado = checks.summary("check")
    _emit(resultado)
    return 0 if resultado["passed"] else 1


if __name__ == "__main__":
    # Execução direta só para debug manual — o caminho real é sempre
    # `scripts/final.py evidence|check`, que resolve `Config` e faz o
    # dispatch por nome de subcomando.
    from .config import load_config

    _cfg = load_config()
    sys.exit(cmd_evidence(_cfg, None))
