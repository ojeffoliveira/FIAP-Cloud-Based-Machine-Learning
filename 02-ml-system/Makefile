# Lab 02 - Do modelo ao sistema de Machine Learning
#
# Todo alvo é idempotente e seguro de rodar duas vezes. A ordem do ciclo de vida
# é doctor -> data -> validate -> apply -> predict -> evaluate -> evidence ->
# destroy -> verify-clean, e `make e2e` roda exatamente isso com uma armadilha
# de limpeza.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# O venv tem preferência sobre o `python3` que o PATH resolver: um shell de login
# recarrega /etc/profile e derruba o .venv/bin que o devcontainer coloca na
# frente, o que rodaria o lab num interpretador sem boto3 nem pyyaml.
PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
TF ?= terraform
TF_DIR := terraform
SCRIPTS := scripts
ARTIFACTS ?= artifacts

# Os scripts importam lab1 de src/; exportar aqui faz o `make` funcionar com um
# interpretador limpo, sem etapa de instalação.
export PYTHONPATH := src

# Escrito por scripts/wait_training.py; carrega a URI comprovada do artefato e
# liga o estágio de serving.
HANDOFF := $(TF_DIR)/artifact.auto.tfvars.json

.PHONY: help doctor data fmt validate plan apply predict evaluate \
        evidence destroy verify-clean e2e clean

help: ## Lista os alvos disponíveis
	@echo "Lab 02 - Do modelo ao sistema de Machine Learning"
	@echo
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Ciclo completo:  make e2e"
	@echo "  Manter recursos: make e2e KEEP_RESOURCES=1   (você precisa rodar make destroy depois)"

doctor: ## Confere versões das ferramentas e credenciais/região/role da AWS
	@echo "== versões das ferramentas =="
	@$(TF) version
	@$(PYTHON) --version
	@$(PYTHON) -c "import boto3, botocore, numpy, sklearn; \
		print(f'boto3 {boto3.__version__} botocore {botocore.__version__} numpy {numpy.__version__} scikit-learn {sklearn.__version__}')"
	@echo "== verificação prévia da AWS =="
	@$(PYTHON) $(SCRIPTS)/check_aws.py > /dev/null

data: ## Gera o dataset determinístico
	@$(PYTHON) $(SCRIPTS)/generate_dataset.py --clean > /dev/null

fmt: ## Formata o Terraform (confere no CI, reescreve localmente)
	@$(TF) fmt -recursive $(TF_DIR)

validate: ## terraform init + validate
	@$(TF) -chdir=$(TF_DIR) init -input=false -upgrade=false
	@$(TF) -chdir=$(TF_DIR) fmt -check -recursive
	@$(TF) -chdir=$(TF_DIR) validate

plan: validate ## Planeja o estágio atual
	@$(TF) -chdir=$(TF_DIR) plan -input=false

apply: data validate ## Provisiona storage + treino e depois o serving (um comando, dois estágios)
	@echo "== estágio 1/2: storage e training job =="
	@rm -f $(HANDOFF)
	@$(TF) -chdir=$(TF_DIR) apply -input=false -auto-approve -var deploy_serving=false
	@echo "== portão: esperar o training job e provar que o artefato existe =="
	@$(PYTHON) $(SCRIPTS)/wait_training.py > /dev/null
	@echo "== estágio 2/2: model, endpoint configuration e endpoint =="
	@$(TF) -chdir=$(TF_DIR) apply -input=false -auto-approve
	@$(TF) -chdir=$(TF_DIR) output

predict: ## Inferência determinística de verificação (smoke)
	@$(PYTHON) $(SCRIPTS)/predict.py > /dev/null

evaluate: ## Pontua o conjunto de teste separado através do endpoint
	@$(PYTHON) $(SCRIPTS)/evaluate_endpoint.py > /dev/null

evidence: ## Monta o pacote de evidências
	@$(PYTHON) $(SCRIPTS)/evidence.py > /dev/null

destroy: ## Destrói todos os recursos gerenciados
	@$(TF) -chdir=$(TF_DIR) destroy -input=false -auto-approve
	@rm -f $(HANDOFF)

verify-clean: ## Prova que não sobrou recurso de serving cobrando
	@$(PYTHON) $(SCRIPTS)/verify_clean.py > /dev/null

e2e: ## Ciclo completo com limpeza à prova de falha (KEEP_RESOURCES=1 pula o destroy)
	@set -euo pipefail; \
	if [ "$${KEEP_RESOURCES:-0}" = "1" ]; then \
		echo "!! KEEP_RESOURCES=1: o endpoint vai continuar de pé e continuar cobrando."; \
		echo "!! Rode 'make destroy' assim que terminar, ou o crédito do lab paga por ele."; \
	else \
		trap 'code=$$?; echo "== limpeza (roda sempre) =="; $(MAKE) --no-print-directory destroy || true; \
			$(MAKE) --no-print-directory verify-clean || true; exit $$code' EXIT; \
	fi; \
	$(MAKE) --no-print-directory doctor; \
	$(MAKE) --no-print-directory apply; \
	$(MAKE) --no-print-directory predict; \
	$(MAKE) --no-print-directory evaluate; \
	$(MAKE) --no-print-directory evidence

clean: ## Remove artefatos locais gerados (nunca toca na AWS)
	@rm -rf $(ARTIFACTS)
	@find . -name __pycache__ -type d -prune -exec rm -rf {} +
