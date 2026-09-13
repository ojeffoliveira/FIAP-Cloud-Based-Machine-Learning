#!/usr/bin/env python3
"""Passo didático de invocação — mostra o payload enviado e a resposta recebida.

Uso:
  python scripts/invoke.py --release v1 --case caso_01
  python scripts/invoke.py --release v1 --context-file meu_contexto.json
  python scripts/invoke.py --release v1 --smoke      # roda todos os casos, só protocolo

O contexto de entrada NUNCA vem de shell livre: só de um `--case` (enum fixo
de `eval/cases.yaml`) ou de `--context-file` (JSON/YAML cujas chaves são
validadas contra a whitelist `inference.ALLOWED_CONTEXT_FIELDS` antes de
qualquer chamada de rede — chave fora da lista é rejeitada, não ignorada).

Sem `--smoke`: modo didático de UMA invocação — imprime o request e o
response completos (o aluno vê exatamente o que sai e o que volta). Com
`--smoke`: roda protocolo (não conteúdo) contra todos os casos de
`eval/cases.yaml` e grava `artifacts/evidence/smoke-<release>.json`.

Convenção do lab: stdout = resultado (JSON), stderr = progresso.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml

from lab42 import evaluation, evidence, inference
from lab42.aws import LabError, emit, log


def _load_context_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise LabError(f"--context-file {path} não existe.")
    texto = path.read_text(encoding="utf-8")
    dados = json.loads(texto) if path.suffix == ".json" else yaml.safe_load(texto)
    if not isinstance(dados, dict):
        raise LabError(
            f"{path} precisa conter um objeto/mapa no topo, não {type(dados).__name__}."
        )
    desconhecidos = sorted(set(dados) - set(inference.ALLOWED_CONTEXT_FIELDS))
    if desconhecidos:
        raise LabError(
            f"{path} tem campo(s) fora da whitelist do contrato de contexto: {desconhecidos}. "
            f"Campos aceitos: {list(inference.ALLOWED_CONTEXT_FIELDS)}."
        )
    if not dados:
        raise LabError(f"{path} não tem nenhum campo de contexto.")
    return dados


def cmd_single(
    release: str, endpoint_override: str | None, contexto: dict[str, Any]
) -> dict:
    endpoint_name = endpoint_override or inference.resolve_endpoint_name(release)
    log(f"invocando {endpoint_name} (release={release})...")
    log(f"contexto: {contexto}")

    try:
        resultado = inference.invoke(endpoint_name, contexto)
    except (inference.InferenceProtocolError, inference.InferenceContentError) as exc:
        log(f"[FAIL] {exc}")
        raise

    log(
        f"[PASS] respondeu em {resultado.latency_s:.3f}s, finish_reason={resultado.finish_reason}"
    )
    return {
        "endpoint_name": endpoint_name,
        "release": release,
        "request": resultado.request,
        "response": resultado.response,
        "text": resultado.text,
        "latency_s": round(resultado.latency_s, 3),
    }


def cmd_smoke(release: str, endpoint_override: str | None) -> dict:
    casos = evaluation.load_cases()
    endpoint_name = endpoint_override or inference.resolve_endpoint_name(release)
    log(
        f"smoke: {len(casos)} casos contra {endpoint_name} (release={release}, só protocolo)..."
    )

    aprovados = 0
    erros_protocolo: list[str] = []
    for caso in casos:
        try:
            inference.invoke(endpoint_name, caso["contexto"])
            aprovados += 1
            log(f"  [PASS] {caso['id']}")
        except inference.InferenceProtocolError as exc:
            erros_protocolo.append(f"{caso['id']}: {exc}")
            log(f"  [FAIL] {caso['id']}: erro de protocolo — {exc}")
        except inference.InferenceContentError as exc:
            # smoke é só sobre transporte; erro de conteúdo aqui é reportado mas
            # não empilhado em `protocol_errors` — schema do evidence pede a
            # categoria separada (ver docstring de InferenceContentError).
            log(f"  [FAIL] {caso['id']}: erro de conteúdo — {exc}")

    resultado = {
        "endpoint_name": endpoint_name,
        "release": release,
        "cases_total": len(casos),
        "cases_passed": aprovados,
        "protocol_errors": erros_protocolo,
    }
    evidence.write(f"smoke-{release}.json", resultado)
    ok = not erros_protocolo and aprovados == len(casos)
    log(
        f"{'[PASS]' if ok else '[FAIL]'} smoke {release}: {aprovados}/{len(casos)} sem erro de protocolo"
    )
    return resultado


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", choices=["v1", "v2"], required=True)
    parser.add_argument("--endpoint", default=None, help="Override do nome do endpoint")
    parser.add_argument("--case", default=None, help="Id de caso em eval/cases.yaml")
    parser.add_argument(
        "--context-file", default=None, help="Arquivo JSON/YAML com o contexto"
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Roda todos os casos, só checando protocolo",
    )
    args = parser.parse_args()

    try:
        if args.smoke:
            if args.case or args.context_file:
                parser.error(
                    "--smoke não combina com --case/--context-file (roda todos os casos)"
                )
            resultado = cmd_smoke(args.release, args.endpoint)
            ok = (
                not resultado["protocol_errors"]
                and resultado["cases_passed"] == resultado["cases_total"]
            )
            emit(resultado)
            return 0 if ok else 1

        if bool(args.case) == bool(args.context_file):
            parser.error("informe exatamente um de --case ou --context-file")

        contexto = (
            evaluation.get_case(args.case)["contexto"]
            if args.case
            else _load_context_file(Path(args.context_file))
        )
        resultado = cmd_single(args.release, args.endpoint, contexto)
        emit(resultado)
        return 0
    except LabError as exc:
        log(f"[FAIL] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
