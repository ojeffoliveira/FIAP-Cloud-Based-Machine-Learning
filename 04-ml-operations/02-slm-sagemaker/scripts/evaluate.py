#!/usr/bin/env python3
"""Roda o gate generativo (spec §8) contra um endpoint real — nunca por exact-match.

Uso:
  python scripts/evaluate.py --release v1
  python scripts/evaluate.py --release v2 --endpoint meu-endpoint-custom
  python scripts/evaluate.py --compare

`--compare` delega para `scripts/compare.py` (dono também é este agente — a
lógica de comparação mora lá porque é reaproveitada como biblioteca; aqui é
só o ponto de entrada que o Makefile chama). Sem `--compare`, `--release` é
obrigatório.

Convenção do lab: stdout carrega só o resultado (JSON), stderr carrega
progresso. Exit code 0 só se todos os casos passaram e não houve violação de
PII/valor inventado — o resto (idioma, tamanho, termo esperado) também
reprova o gate, mas PII é a única categoria citada em mensagem própria de
[FAIL], porque é a regra dura do lab ("agente morto").
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import compare  # script vizinho, ver sys.path.insert acima

from lab42 import evaluation, evidence
from lab42.aws import LabError, emit, log


def cmd_evaluate(release: str, endpoint_override: str | None) -> dict:
    resultado = evaluation.run_evaluation(release, endpoint_name=endpoint_override)
    evidence.write(f"evaluation-{release}.json", resultado)
    return resultado


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", choices=["v1", "v2"], default=None)
    parser.add_argument(
        "--endpoint",
        default=None,
        help="Override do nome do endpoint (normalmente derivado de config/lab.yaml)",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compara v1 x v2 em vez de avaliar uma release",
    )
    args = parser.parse_args()

    if args.compare:
        payload = compare.run()
        print(compare.render_table(payload["comparacao"], payload))
        return 0

    if not args.release:
        parser.error("--release é obrigatório quando --compare não é usado")

    try:
        resultado = cmd_evaluate(args.release, args.endpoint)
    except LabError as exc:
        log(f"[FAIL] {exc}")
        return 1

    pii = resultado["pii_violations"]
    total, aprovados = resultado["cases_total"], resultado["cases_passed"]
    if pii > 0:
        log(
            f"[FAIL] {pii} caso(s) com PII/valor inventado — regra dura do lab, gate reprovado."
        )
    ok = aprovados == total and pii == 0
    log(
        f"{'[PASS]' if ok else '[FAIL]'} release={args.release}: "
        f"{aprovados}/{total} casos aprovados, {pii} violação(ões) de PII/valor"
    )
    emit(resultado)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
