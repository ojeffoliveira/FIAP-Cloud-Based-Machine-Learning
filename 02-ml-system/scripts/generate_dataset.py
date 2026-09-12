#!/usr/bin/env python3
"""Gera o dataset sintético determinístico.

Mesma semente, mesmos bytes: o manifesto registra um SHA-256 por arquivo, então
uma segunda execução - em outra máquina, no Codespaces, no semestre que vem -
pode ser *provada* idêntica em vez de presumida idêntica.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab1.config import DATA_DIR, emit, load_config, log
from lab1.dataset import write_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DATA_DIR, help="diretório de saída")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="apaga o diretório de saída antes, para provar a geração do zero",
    )
    args = parser.parse_args()

    cfg = load_config()
    if args.clean and args.out.exists():
        log(f"[data] removendo {args.out}")
        shutil.rmtree(args.out)

    manifest = write_dataset(cfg, args.out)

    log(f"[data] semente {manifest['seed']} schema {manifest['schema_version']}")
    log(f"[data] source {manifest['source']['rows']} linhas, prevalência {manifest['source']['prevalence']}")
    for name, split in manifest["splits"].items():
        log(f"[data] {name:<10} {split['rows']:>5} linhas  prevalência {split['prevalence']}  {split['sha256'][:12]}")
    log(f"[data] escrito em {args.out}")

    emit(manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
