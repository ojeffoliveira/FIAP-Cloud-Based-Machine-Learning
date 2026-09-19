#!/usr/bin/env python3
"""Escreve a tabela de evidências no DECISION.md e repete no terminal.

Uso: python scripts/resumo.py

Lê `artifacts/evidence/{smoke-v1,evaluation-v1,evaluation-v2,benchmark,
credential-source-v2,workflow-run}.json` (só os que já existem — nunca fabrica
número de execução que não rodou), regrava a tabela entre os marcadores do
`DECISION.md` e imprime as mesmas frases no terminal.

Nunca toca a AWS: só lê JSON local e escreve markdown local.

Convenção do lab: stdout = resultado (JSON das frases); stderr = narração.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab42 import resumo
from lab42.aws import emit


def main() -> int:
    frases = resumo.run()
    emit(frases)
    return 0


if __name__ == "__main__":
    sys.exit(main())
