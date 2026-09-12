#!/usr/bin/env python3
"""Roda o contrato de dados. Sai com código diferente de zero em qualquer violação.

Isso roda antes de qualquer coisa tocar a AWS: um dataset que reprova aqui nunca
se torna um training job, então nenhum aluno paga compute por dado que ninguém
conferiu.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab1.config import DATA_DIR, emit, load_config, load_schema, log
from lab1.data_contract import validate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    cfg, schema = load_config(), load_schema()
    report = validate(cfg, schema, args.data)

    log(f"contrato de dados: {len(report.checks)} verificações contra {args.data}")
    for check in report.checks:
        log(f"  [{'PASS' if check.passed else 'FAIL'}] {check.name}: {check.detail}")
    log(f"[{'PASS' if report.ok else 'FAIL'}] {len(report.failed)} de {len(report.checks)} verificações reprovaram")

    emit(report.as_dict())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
