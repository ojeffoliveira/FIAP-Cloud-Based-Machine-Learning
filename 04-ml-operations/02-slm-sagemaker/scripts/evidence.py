#!/usr/bin/env python3
"""Consolida o dossiê de evidência do Lab 04.2 em artifacts/evidence/.

Não fabrica nenhum dado: lê o que os outros comandos já escreveram (spec §14),
gera `manifest.json` (o índice honesto do que existe e do que falta) e
`evidence.md` (o relatório humano, cada linha citando arquivo e campo).

Uso: python scripts/evidence.py
Saída: stdout = manifest.json; stderr = progresso.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab42 import aws, evidence
from lab42.aws import LabError, emit, log


def main() -> int:
    aws.ensure_dirs()

    try:
        conta = aws.account_id()
        regiao = aws.region()
    except LabError as exc:
        log(f"[aviso] não foi possível confirmar conta/região via STS: {exc}")
        log(
            "[aviso] o dossiê é gerado mesmo assim, com conta/região marcadas como desconhecidas."
        )
        conta, regiao = "desconhecida", aws.region()

    log("[evidence] lendo arquivos existentes em artifacts/evidence/")
    collected = evidence.collect()

    faltando = [nome for nome, dados in collected.items() if dados is None]
    presentes = [nome for nome, dados in collected.items() if dados is not None]
    log(f"[evidence] presentes: {len(presentes)}/{len(collected)}")
    for nome in faltando:
        log(f"[evidence] faltando: {nome} (gerado por: {evidence.STAGE_FILES[nome]})")

    manifest = evidence.build_manifest(collected, conta=conta, regiao=regiao)
    manifest_path = evidence.write("manifest.json", manifest)
    log(f"[evidence] escrito {manifest_path}")

    markdown = evidence.render_markdown(collected, conta=conta, regiao=regiao)
    markdown_path = aws.EVIDENCE_DIR / "evidence.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    log(f"[evidence] escrito {markdown_path}")

    if not manifest["completo"]:
        log(
            "[evidence] dossiê parcial: normal antes do e2e completo (V1+V2). "
            "`make evidence` sempre grava o que existe, sem fabricar o resto."
        )

    emit(manifest)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LabError as exc:
        log(f"[FAIL] {exc}")
        sys.exit(1)
