# Lab 04.1 - Observabilidade, drift e resposta operacional
#
# Um endpoint verde não prova um modelo correto. Ordem do ciclo de vida:
# setup -> doctor -> data -> validate-data -> apply -> dashboard -> status ->
# baseline -> drift -> alarm-status -> reaction -> ground-truth -> evidence ->
# destroy -> verify-clean. `make e2e` roda exatamente isso com uma armadilha de
# limpeza.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
TF ?= terraform
TF_DIR := terraform
SCRIPTS := scripts
ARTIFACTS ?= artifacts
BUILD ?= build

export PYTHONPATH := src
# Exportado para o lab.py usar o MESMO binário do Terraform que o make usa, ao ler
# os outputs do state. Sem isso, uma máquina com duas versões instaladas leria o
# state com uma e aplicaria com a outra.
export TF

HANDOFF := $(TF_DIR)/artifact.auto.tfvars.json

.PHONY: help setup doctor data validate-data test fmt validate plan apply status \
        dashboard baseline drift alarm-status reaction ground-truth evidence \
        destroy verify-clean e2e clean

help: ## Lista os alvos disponíveis
	@echo "Lab 04.1 - Observabilidade, drift e resposta operacional"
	@echo
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Ciclo completo:  make e2e"
	@echo "  Manter recursos: make e2e KEEP_RESOURCES=1   (você precisa rodar make destroy depois)"

setup: ## Cria/atualiza o .venv do lab com as dependências fixadas
	@bash $(SCRIPTS)/setup.sh

doctor: setup ## Confere ferramentas, credencial/região/role e alcance dos serviços
	@$(PYTHON) $(SCRIPTS)/lab.py doctor > /dev/null

data: setup ## Gera o dataset determinístico e as duas janelas de produção
	@$(PYTHON) $(SCRIPTS)/lab.py data > /dev/null

validate-data: data ## Roda o contrato de dados executável e imprime os hashes exatos
	@$(PYTHON) $(SCRIPTS)/lab.py validate-data

test: setup ## Roda os testes unitários (PSI, contrato de dados, handler da Lambda)
	@$(PYTHON) -m pytest -q tests

fmt: ## Formata o Terraform (confere no CI, reescreve localmente)
	@$(TF) fmt -recursive $(TF_DIR)

validate: ## terraform init + fmt -check + validate
	@$(TF) -chdir=$(TF_DIR) init -input=false -upgrade=false
	@$(TF) -chdir=$(TF_DIR) fmt -check -recursive
	@$(TF) -chdir=$(TF_DIR) validate

plan: validate-data validate ## Planeja o estágio atual
	@$(TF) -chdir=$(TF_DIR) plan -input=false

apply: validate-data validate ## Provisiona storage + treino, portão, e então endpoint + dashboard + alarme + reação
	@echo "== estágio 1/2: storage, dataset e training job =="
	@rm -f $(HANDOFF)
	@mkdir -p $(BUILD)
	@$(TF) -chdir=$(TF_DIR) apply -input=false -auto-approve -var deploy_serving=false
	@echo "== portão: esperar o training job e provar que o artefato existe =="
	@$(PYTHON) $(SCRIPTS)/lab.py wait-training > /dev/null
	@echo "== estágio 2/2: model, endpoint, dashboard, alarme, EventBridge e Lambda =="
	@$(TF) -chdir=$(TF_DIR) apply -input=false -auto-approve
	@$(TF) -chdir=$(TF_DIR) output

status: ## Descreve endpoint, alarme, dashboard, Lambda e regra do EventBridge em JSON
	@$(PYTHON) $(SCRIPTS)/lab.py status

dashboard: ## Imprime o nome e o link direto do dashboard do CloudWatch
	@$(PYTHON) $(SCRIPTS)/lab.py dashboard

baseline: ## Observa a janela saudável, publica as métricas e prova que o alarme está OK
	@$(PYTHON) $(SCRIPTS)/lab.py baseline > /dev/null

drift: ## Observa a janela deslocada, publica o drift e nomeia as variáveis responsáveis
	@$(PYTHON) $(SCRIPTS)/lab.py drift > /dev/null

alarm-status: ## Mostra o estado do alarme de drift e as transições recentes
	@$(PYTHON) $(SCRIPTS)/lab.py alarm-status > /dev/null

reaction: ## Espera o incidente que a Lambda escreve no S3 e valida o conteúdo
	@$(PYTHON) $(SCRIPTS)/lab.py reaction > /dev/null

ground-truth: ## Avalia a qualidade com o rótulo que chegou depois e publica F1/ROC-AUC
	@$(PYTHON) $(SCRIPTS)/lab.py ground-truth > /dev/null

evidence: ## Consolida o dossiê conferível em artifacts/evidence/
	@$(PYTHON) $(SCRIPTS)/lab.py evidence > /dev/null

destroy: ## Destrói todos os recursos gerenciados
	@$(TF) -chdir=$(TF_DIR) destroy -input=false -auto-approve
	@rm -f $(HANDOFF)

verify-clean: ## Prova por consulta direta à API que não sobrou nada cobrando
	@$(PYTHON) $(SCRIPTS)/lab.py verify-clean

e2e: ## Ciclo completo com limpeza à prova de falha (KEEP_RESOURCES=1 pula o destroy)
	@set -euo pipefail; \
	if [ "$${KEEP_RESOURCES:-0}" = "1" ]; then \
		echo "!! KEEP_RESOURCES=1: o endpoint real-time vai continuar de pé e continuar cobrando."; \
		echo "!! Rode 'make destroy' assim que terminar, ou o crédito do lab paga por ele."; \
	else \
		trap 'code=$$?; echo "== limpeza (roda sempre) =="; $(MAKE) --no-print-directory destroy || true; \
			$(MAKE) --no-print-directory verify-clean || true; exit $$code' EXIT; \
	fi; \
	$(MAKE) --no-print-directory doctor; \
	$(MAKE) --no-print-directory test; \
	$(MAKE) --no-print-directory apply; \
	$(MAKE) --no-print-directory dashboard; \
	$(MAKE) --no-print-directory status; \
	$(MAKE) --no-print-directory baseline; \
	$(MAKE) --no-print-directory drift; \
	$(PYTHON) $(SCRIPTS)/lab.py alarm-status --wait-for-alarm > /dev/null; \
	$(MAKE) --no-print-directory reaction; \
	$(MAKE) --no-print-directory ground-truth; \
	$(MAKE) --no-print-directory evidence

clean: ## Remove artefatos locais gerados (nunca toca na AWS)
	@rm -rf $(ARTIFACTS) $(BUILD)
	@find . -name __pycache__ -type d -prune -exec rm -rf {} +
