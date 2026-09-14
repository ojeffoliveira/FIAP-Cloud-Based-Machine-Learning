#!/usr/bin/env bash
# Cria/atualiza o .venv do Trabalho Final e gera o student_id persistente.
# Idempotente: seguro rodar em todo `make bootstrap`/`make deploy`.
#
# Progresso vai para stderr: quem capturar a saída deste script quer o
# resultado, não a narração.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

log() { echo "==> $*" >&2; }

# Respeita o TF que o Makefile exporta, para este script reportar o MESMO
# binário que o resto do trabalho vai usar.
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

# student_id nomeia todo recurso AWS deste grupo e precisa ser o mesmo em
# toda chamada de make daqui até o `make finish` - gerar de novo a cada
# execução criaria recursos com nomes novos e deixaria os antigos órfãos,
# cobrando sem ninguém saber. Por isso é gravado uma única vez.
mkdir -p .generated
STUDENT_ID_FILE=".generated/student_id"
if [ ! -f "$STUDENT_ID_FILE" ]; then
  raw="$(git config user.email 2>/dev/null || whoami)"
  student_id="$(echo "$raw" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/@.*$//; s/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' \
    | cut -c1-20)"
  [ -n "$student_id" ] || student_id="aluno"
  echo "$student_id" > "$STUDENT_ID_FILE"
  log "student_id gerado: $student_id (gravado em $STUDENT_ID_FILE)"
fi

log "terraform  : $("$TERRAFORM" version | head -1)"
log "python     : $(.venv/bin/python --version)"
log "student_id : $(cat "$STUDENT_ID_FILE")"
log "pronto. Próximo passo: make doctor"
