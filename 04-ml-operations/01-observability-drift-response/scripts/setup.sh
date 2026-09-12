#!/usr/bin/env bash
# Cria/atualiza o .venv deste laboratório. Idempotente: seguro rodar duas vezes.
#
# Progresso vai para stderr: quem capturar a saída deste script quer o resultado,
# não a narração.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

log() { echo "==> $*" >&2; }

# Respeita o TF que o Makefile exporta, para este script reportar o MESMO binário
# que o resto do lab vai usar. Numa máquina com duas versões instaladas, reportar o
# do PATH enquanto o lab usa outro é pior que não reportar nada.
TERRAFORM="${TF:-terraform}"

if ! command -v "$TERRAFORM" >/dev/null 2>&1 && [ ! -x "$TERRAFORM" ]; then
  log "terraform não encontrado. Instale pelo mecanismo padrão da disciplina antes de continuar."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  log "criando .venv"
  python3 -m venv .venv
fi

.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

log "terraform : $("$TERRAFORM" version | head -1)"
log "python    : $(.venv/bin/python --version)"
log "pronto. Próximo passo: make doctor"
