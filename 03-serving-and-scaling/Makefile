# Lab 03 - Serving and Scaling
#
# Um modelo, quatro formas de consumir. Ordem do ciclo de vida: setup -> doctor
# -> data -> validate-data -> apply -> status/compare/async/batch/load/
# scale-demo -> evidence -> destroy -> verify-clean. `make e2e` roda exatamente
# isso com uma armadilha de limpeza.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
TF ?= terraform
TF_DIR := terraform
SCRIPTS := scripts
ARTIFACTS ?= artifacts

export PYTHONPATH := src

HANDOFF := $(TF_DIR)/artifact.auto.tfvars.json

.PHONY: help setup doctor data validate-data fmt validate plan apply status \
        compare async batch load scale-demo evidence destroy verify-clean \
        e2e clean

help: ## Lista os alvos disponíveis
	@echo "Lab 03 - Serving and Scaling"
	@echo
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Ciclo completo:  make e2e"
	@echo "  Manter recursos: make e2e KEEP_RESOURCES=1   (você precisa rodar make destroy depois)"

setup: ## Cria/atualiza o .venv do lab com as dependências fixadas
	@bash $(SCRIPTS)/setup.sh

doctor: setup ## Confere versões das ferramentas e credenciais/região/role da AWS
	@$(PYTHON) $(SCRIPTS)/lab.py doctor > /dev/null

data: setup ## Gera o dataset determinístico
	@$(PYTHON) $(SCRIPTS)/lab.py data > /dev/null

validate-data: data ## Roda o contrato de dados executável e imprime os hashes exatos
	@$(PYTHON) $(SCRIPTS)/lab.py validate-data

fmt: ## Formata o Terraform (confere no CI, reescreve localmente)
	@$(TF) fmt -recursive $(TF_DIR)

validate: ## terraform init + validate
	@$(TF) -chdir=$(TF_DIR) init -input=false -upgrade=false
	@$(TF) -chdir=$(TF_DIR) fmt -check -recursive
	@$(TF) -chdir=$(TF_DIR) validate

plan: validate-data validate ## Planeja o estágio atual
	@$(TF) -chdir=$(TF_DIR) plan -input=false

apply: validate-data validate ## Provisiona storage + bootstrap de treino, portão, e então 3 endpoints + autoscaling
	@echo "== estágio 1/2: storage e bootstrap de treino =="
	@rm -f $(HANDOFF)
	@$(TF) -chdir=$(TF_DIR) apply -input=false -auto-approve -var deploy_serving=false
	@echo "== portão: esperar o training job e provar que o artefato existe =="
	@$(PYTHON) $(SCRIPTS)/lab.py wait-training > /dev/null
	@echo "== estágio 2/2: model, 3 endpoint configs/endpoints, autoscaling =="
	@$(TF) -chdir=$(TF_DIR) apply -input=false -auto-approve
	@$(TF) -chdir=$(TF_DIR) output

status: ## Descreve endpoints, configs e scalable targets em JSON
	@$(PYTHON) $(SCRIPTS)/lab.py status

dashboard: ## Imprime os links dos dois painéis do CloudWatch (o do lab e o de observação ao vivo)
	@$(PYTHON) $(SCRIPTS)/lab.py dashboard

resumo: ## Preenche a tabela de evidências do DECISION.md e imprime os números medidos
	@$(PYTHON) $(SCRIPTS)/lab.py resumo >/dev/null

compare: ## Smoke + latência, real-time vs serverless (DURACAO=180 mantém tráfego por 3 min)
	@$(PYTHON) $(SCRIPTS)/lab.py compare $(if $(DURACAO),--duracao $(DURACAO),)

async: ## Sobe o payload para o S3, InvokeEndpointAsync, espera e valida a saída
	@$(PYTHON) $(SCRIPTS)/lab.py async

batch: ## CreateTransformJob para as 600 linhas de teste, espera e valida as 600 saídas
	@$(PYTHON) $(SCRIPTS)/lab.py batch

load: ## Carga no real-time com concorrência 1/4/8 (DURACAO=180 dá um minuto a cada nível)
	@$(PYTHON) $(SCRIPTS)/lab.py load $(if $(DURACAO),--duracao $(DURACAO),)

scale-demo: ## Prova 1->2->1 instâncias e restaura (DURACAO=120 segura o degrau e gera tráfego de fundo)
	@$(PYTHON) $(SCRIPTS)/lab.py scale-demo $(if $(DURACAO),--duracao $(DURACAO),)

evidence: ## Consolida os resultados conferíveis em artifacts/evidence/
	@$(PYTHON) $(SCRIPTS)/lab.py evidence > /dev/null

destroy: ## Destrói todos os recursos gerenciados
	@$(TF) -chdir=$(TF_DIR) destroy -input=false -auto-approve
	@rm -f $(HANDOFF)

verify-clean: ## Prova por consulta direta à API que não sobrou nada cobrando com o prefixo deste lab
	@$(PYTHON) $(SCRIPTS)/lab.py verify-clean

e2e: ## Ciclo completo com limpeza à prova de falha (KEEP_RESOURCES=1 pula o destroy)
	@set -euo pipefail; \
	if [ "$${KEEP_RESOURCES:-0}" = "1" ]; then \
		echo "!! KEEP_RESOURCES=1: os endpoints real-time e async vão continuar de pé e continuar cobrando."; \
		echo "!! Rode 'make destroy' assim que terminar, ou o crédito do lab paga por eles."; \
	else \
		trap 'code=$$?; echo "== limpeza (roda sempre) =="; $(MAKE) --no-print-directory destroy || true; \
			$(MAKE) --no-print-directory verify-clean || true; exit $$code' EXIT; \
	fi; \
	$(MAKE) --no-print-directory doctor; \
	$(MAKE) --no-print-directory apply; \
	$(MAKE) --no-print-directory status; \
	$(MAKE) --no-print-directory compare; \
	$(MAKE) --no-print-directory async; \
	$(MAKE) --no-print-directory batch; \
	$(MAKE) --no-print-directory load; \
	$(MAKE) --no-print-directory scale-demo; \
	$(MAKE) --no-print-directory evidence

clean: ## Remove artefatos locais gerados (nunca toca na AWS)
	@rm -rf $(ARTIFACTS)
	@find . -name __pycache__ -type d -prune -exec rm -rf {} +
