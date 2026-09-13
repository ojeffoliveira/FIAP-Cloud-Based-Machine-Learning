#!/usr/bin/env python3
"""Compara V1 (Q4_0) x V2 (Q4_K_M) e monta a evidência de recomendação de release.

Uso:
  python scripts/compare.py

`make compare` chama `evaluate.py --compare`, que importa e delega para este
módulo — a lógica de comparação mora aqui (dono: A8) porque é reaproveitada
como biblioteca; `evaluate.py` é só o ponto de entrada do CLI (spec §17).

Contrato duro deste comparador: a diferença de quantização (Q4_0 x Q4_K_M)
NUNCA é, por si só, motivo de recomendação. `recommend()` só cita quantização
como um dado de tamanho de artefato — a recomendação em si é sempre apoiada
em evidência medida (latência real do benchmark, taxa de sucesso real da
avaliação). Sem essa evidência para as duas releases, a resposta honesta é
"ainda não dá para comparar", não um chute a favor de V2.

Saída: stdout = tabela + JSON da comparação; stderr = progresso.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab42 import evidence, hf_release
from lab42.aws import log

_RELEASES = ("v1", "v2")

# Cada V é servida por um caminho de credencial diferente por decisão de
# segurança do lab (spec §11): V1 sai da máquina do aluno (Codespaces,
# credencial temporária do Academy); V2 sai de um runner self-hosted efêmero
# em EC2 assumindo `LabInstanceProfile` via IMDSv2 — nunca uma chave estática
# no GitHub. Fixo aqui porque não existe evidência JSON própria para "de onde
# saiu o comando" no caso V1 (é óbvio: rodou na mesma máquina que roda este
# script); V2 tem `credential-source-v2.json` como prova.
_CREDENTIAL_SOURCE = {
    "v1": "Codespaces do aluno — credencial temporária do AWS Academy (~/.aws/credentials)",
    "v2": "runner self-hosted efêmero (EC2) — IMDSv2 + LabInstanceProfile, sem chave estática no GitHub",
}
_PIPELINE_PROVENANCE = {
    "v1": "manual — `make model-v1` + `make deploy-v1` na máquina do aluno",
    "v2": "pipeline — workflow `04-2-deploy-v2.yml` (self-hosted runner)",
}


def _manifest(release: str) -> dict[str, Any] | None:
    try:
        return hf_release.load_manifest(hf_release.manifest_path(release))
    except (FileNotFoundError, OSError):
        return None


def build_comparison() -> dict[str, Any]:
    """Reúne, sem inventar nada, toda evidência já escrita em artifacts/evidence/."""
    dados: dict[str, Any] = {}
    bench = evidence.read("benchmark.json") or {}
    for release in _RELEASES:
        manifest = _manifest(release)
        avaliacao = evidence.read(f"evaluation-{release}.json")
        bench_release = bench.get(release)
        dados[release] = {
            "manifest_presente": manifest is not None,
            "quantization": manifest["model"]["quantization"] if manifest else None,
            "artifact_size_bytes": manifest["model"]["size_bytes"]
            if manifest
            else None,
            "prompt_contract_version": manifest.get("prompt_contract_version")
            if manifest
            else None,
            "avaliacao": (
                {
                    "cases_total": avaliacao["cases_total"],
                    "cases_passed": avaliacao["cases_passed"],
                    "pii_violations": avaliacao["pii_violations"],
                }
                if avaliacao
                else None
            ),
            "benchmark": (
                {
                    "success_rate": bench_release.get("success_rate"),
                    "p50_ms": bench_release.get("p50_ms"),
                    "p95_ms": bench_release.get("p95_ms"),
                    "tokens_per_s": bench_release.get("tokens_per_s"),
                    "instance_type": bench_release.get("instance_type"),
                }
                if bench_release
                else None
            ),
            "pipeline_provenance": _PIPELINE_PROVENANCE[release],
            "credential_source": _CREDENTIAL_SOURCE[release],
        }
    return dados


def recommend(dados: dict[str, Any]) -> dict[str, Any]:
    """Recomenda uma release com base só no que foi medido — nunca em quantização isolada."""
    v1, v2 = dados["v1"], dados["v2"]
    razoes: list[str] = []

    evidencia_completa = all(
        d["avaliacao"] is not None and d["benchmark"] is not None for d in (v1, v2)
    )
    if not evidencia_completa:
        faltando = [
            r
            for r in _RELEASES
            if dados[r]["avaliacao"] is None or dados[r]["benchmark"] is None
        ]
        razoes.append(
            "evidência incompleta para "
            + " e ".join(faltando)
            + " (rode `make quality`/`make benchmark-v1` e o equivalente V2 antes de comparar) — "
            "recomendação abaixo é provisória, baseada só no que existe hoje."
        )
        # Provisório: prioriza a release com evidência real; nunca escolhe uma
        # release só porque "ainda não foi medida" (isso seria inventar uma
        # vantagem, não relatar uma).
        candidatos = [r for r in _RELEASES if dados[r]["avaliacao"] is not None]
        if candidatos:
            escolhida = candidatos[0]
            razoes.append(
                f"{escolhida} é a única com avaliação real registrada até agora."
            )
        else:
            escolhida = "v1"
            razoes.append(
                "nenhuma das duas releases tem avaliação real ainda — v1 citada como "
                "padrão provisório (release manual, sem dependência de pipeline) até "
                "que `make quality`/`make compare` rodem com evidência real de alguma delas."
            )
        return {"recommended_release": escolhida, "reasons": razoes}

    for release in _RELEASES:
        av = dados[release]["avaliacao"]
        if av["pii_violations"] > 0:
            razoes.append(
                f"{release} descartada: {av['pii_violations']} violação(ões) de PII/valor inventado."
            )

    candidatos = [
        r for r in _RELEASES if dados[r]["avaliacao"]["pii_violations"] == 0
    ] or list(_RELEASES)

    def taxa_sucesso(r: str) -> float:
        av = dados[r]["avaliacao"]
        return av["cases_passed"] / av["cases_total"] if av["cases_total"] else 0.0

    melhor_taxa = max(taxa_sucesso(r) for r in candidatos)
    candidatos = [r for r in candidatos if taxa_sucesso(r) == melhor_taxa]
    razoes.append(
        "taxa de sucesso da avaliação: "
        + ", ".join(f"{r}={taxa_sucesso(r):.0%}" for r in _RELEASES)
    )

    if len(candidatos) > 1:
        # Empate em taxa de sucesso: só aí a latência decide, e ainda assim é
        # uma escolha explícita de critério (menor p95), não "V2 vence porque
        # é a quantização mais nova".
        candidatos.sort(key=lambda r: dados[r]["benchmark"]["p95_ms"])
        razoes.append(
            "empate na avaliação — critério de desempate foi menor p95 medido: "
            + ", ".join(f"{r}={dados[r]['benchmark']['p95_ms']}ms" for r in _RELEASES)
        )

    escolhida = candidatos[0]
    razoes.append(
        f"tamanho do artefato ({dados[escolhida]['quantization']}, "
        f"{dados[escolhida]['artifact_size_bytes']} bytes) é dado de contexto, não critério de escolha."
    )
    return {"recommended_release": escolhida, "reasons": razoes}


def render_table(dados: dict[str, Any], recomendacao: dict[str, Any]) -> str:
    linhas = [
        f"{'campo':<28}{'v1 (Q4_0)':<30}{'v2 (Q4_K_M)':<30}",
        "-" * 88,
    ]

    def fmt(v: Any) -> str:
        return "—" if v is None else str(v)

    campos = [
        ("quantization", lambda d: d["quantization"]),
        ("artifact_size_bytes", lambda d: d["artifact_size_bytes"]),
        ("prompt_contract_version", lambda d: d["prompt_contract_version"]),
        (
            "cases_passed/total",
            lambda d: (
                f"{d['avaliacao']['cases_passed']}/{d['avaliacao']['cases_total']}"
                if d["avaliacao"]
                else None
            ),
        ),
        (
            "pii_violations",
            lambda d: d["avaliacao"]["pii_violations"] if d["avaliacao"] else None,
        ),
        ("p50_ms", lambda d: d["benchmark"]["p50_ms"] if d["benchmark"] else None),
        ("p95_ms", lambda d: d["benchmark"]["p95_ms"] if d["benchmark"] else None),
        (
            "success_rate",
            lambda d: d["benchmark"]["success_rate"] if d["benchmark"] else None,
        ),
        (
            "tokens_per_s",
            lambda d: d["benchmark"]["tokens_per_s"] if d["benchmark"] else None,
        ),
    ]
    for nome, extrator in campos:
        linhas.append(
            f"{nome:<28}{fmt(extrator(dados['v1'])):<30}{fmt(extrator(dados['v2'])):<30}"
        )

    linhas += ["", "-- proveniência (não é critério de escolha, é contexto) --"]
    for nome, chave in (
        ("pipeline_provenance", "pipeline_provenance"),
        ("credential_source", "credential_source"),
    ):
        linhas.append(f"v1.{nome}: {dados['v1'][chave]}")
        linhas.append(f"v2.{nome}: {dados['v2'][chave]}")

    linhas += [
        "",
        f"recomendação: {recomendacao['recommended_release']}",
        *[f"  - {razao}" for razao in recomendacao["reasons"]],
    ]
    return "\n".join(linhas)


def run() -> dict[str, Any]:
    log("comparando v1 x v2 a partir de artifacts/evidence/ e model/releases/...")
    dados = build_comparison()
    recomendacao = recommend(dados)
    payload = {**recomendacao, "comparacao": dados}
    evidence.write("release-recommendation.json", payload)
    log(
        f"[PASS] recomendação escrita em artifacts/evidence/release-recommendation.json: {recomendacao['recommended_release']}"
    )
    return payload


def main() -> int:
    payload = run()
    print(render_table(payload["comparacao"], payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
