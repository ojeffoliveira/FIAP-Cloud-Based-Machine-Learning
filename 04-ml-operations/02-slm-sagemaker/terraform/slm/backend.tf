# Backend S3 PARCIAL de propósito: um bloco `backend` do Terraform não aceita
# variável nem interpolação, e o nome do bucket depende de account_id+owner
# do GitHub (nunca hardcodável — quebraria em qualquer fork diferente do
# repositório). Por isso bucket/key/region/use_lockfile vêm de
# `-backend-config=.generated/backend.hcl`, gerado por
# `scripts/bootstrap_backend.py` (que também cria o bucket, com
# versioning+encryption+block public access, ANTES deste init rodar).
#
# `use_lockfile = true` é o mecanismo de lock nativo do backend S3
# (Terraform >= 1.10, aqui travado em 1.15.8) — substitui a tabela DynamoDB
# de lock que versões antigas exigiam. Este lab não cria DynamoDB (regra
# dura da spec).
#
# Sequência real (rodar a partir da raiz do lab, 02-slm-sagemaker/):
#   python scripts/bootstrap_backend.py
#   terraform -chdir=terraform/slm init -backend-config=../../.generated/backend.hcl
#
# O path de -backend-config, com -chdir, resolve relativo ao diretório de
# DESTINO do -chdir (terraform/slm) — não ao cwd original de onde o comando
# foi chamado. Confirmado empiricamente nesta conta/versão do Terraform
# (1.15.8); por isso "../../" e não só ".generated/backend.hcl".
terraform {
  backend "s3" {}
}
