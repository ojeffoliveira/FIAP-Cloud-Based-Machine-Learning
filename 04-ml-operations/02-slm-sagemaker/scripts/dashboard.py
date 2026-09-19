#!/usr/bin/env python3
"""Imprime o link do dashboard CloudWatch deste lab, confirmado por GetDashboard.

Uso: python scripts/dashboard.py

Lê `dashboard_name`/`dashboard_url` de `terraform -chdir=terraform/slm output`
(outputs já publicados em outputs_slm.tf) e chama `GetDashboard` antes de
imprimir — link que abre 404 é pior que não imprimir nada (spec-visual §2).

Convenção do lab: stdout = URL (o dado que alguém pipa); stderr = narração.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab42 import aws
from lab42.aws import LabError, log


def main() -> int:
    outputs = aws.terraform_outputs(aws.TERRAFORM_SLM_DIR)
    nome = outputs.get("dashboard_name") or ""
    url = outputs.get("dashboard_url") or ""
    if not nome:
        raise LabError(
            "o output `dashboard_name` não existe em terraform/slm. Rode `make "
            "deploy-v1` primeiro — o dashboard sobe junto com o endpoint V1."
        )

    corpo = json.loads(aws.get_dashboard(nome)["DashboardBody"])
    widgets = len(corpo.get("widgets", []))

    log("")
    log(f"  Painel do lab : {nome} ({widgets} widgets, janela de 3 horas)")
    log("")
    log("  Deixe o painel aberto do começo ao fim da Parte 8. Ele nasce com a forma")
    log("  final — linhas de V1 e V2 lado a lado — desde o `deploy-v1`; os quadros")
    log("  de V2 ficam vazios até o endpoint V2 existir e receber tráfego. Isso é o")
    log("  comportamento esperado, não erro: se um quadro estiver vazio depois de")
    log("  gerar tráfego, recarregue a aba depois de um ou dois minutos — a métrica")
    log("  do SageMaker é publicada com atraso.")
    log("")
    print(url)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LabError as exc:
        log(f"[FAIL] {exc}")
        sys.exit(1)
