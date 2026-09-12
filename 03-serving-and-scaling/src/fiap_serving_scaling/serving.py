"""Helpers de CSV<->requisição compartilhados por compare/async/batch/load."""

from __future__ import annotations

from pathlib import Path


def read_csv_rows(path: Path) -> list[str]:
    """Devolve as linhas cruas do CSV (sem cabeçalho esperado), sem linhas vazias."""
    with open(path, encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def rows_to_body(rows: list[str]) -> str:
    return "\n".join(rows)
