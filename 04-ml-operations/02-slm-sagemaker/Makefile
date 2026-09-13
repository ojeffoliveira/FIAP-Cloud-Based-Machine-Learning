# Lab 04.2 - Do score a acao: SLM, releases e CI/CD sem chaves AWS
#
# Duas trilhas de credencial, um unico contrato de nomes/targets:
#   V1 (manual, Codespaces)  -> setup -> doctor -> preflight -> backend ->
#     model-v1 -> plan-v1 -> deploy-v1 -> status-v1 -> smoke-v1 ->
#     benchmark-v1 -> quality
#   V2 (pipeline, runner efemero) -> runner-plan -> runner-apply ->
#     runner-register-help -> (push dispara .github/workflows/04-2-deploy-v2.yml
#     no runner self-hosted, sem chave AWS estatica no GitHub) -> compare
# Fechamento: evidence -> destroy-models -> runner-destroy -> destroy-all ->
# verify-clean. `make e2e` roda o que e 100% Codespaces (V1 + infra + qualidade
# local); nunca inventa um PAT para automatizar o registro do runner.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
TF ?= terraform
SCRIPTS := scripts
ARTIFACTS ?= artifacts
GENERATED ?= .generated

export PYTHONPATH := src
# Mesma razao do 04.1: um so binario de terraform para make, scripts Python e
# para quem le os outputs do state (src/lab42/aws.py:terraform_outputs).
export TF

.PHONY: help setup doctor preflight backend model-v1 model-v2 \
        runner-plan runner-apply runner-status runner-register-help \
        plan-v1 deploy-v1 status-v1 smoke-v1 benchmark-v1 quality \
        pipeline-preflight autoscaling-status compare evidence \
        destroy-models runner-destroy destroy-all verify-clean e2e clean

help: ## Lista os alvos disponiveis
	@echo "Lab 04.2 - Do score a acao: SLM, releases e CI/CD sem chaves AWS"
	@echo
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-24s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Trilha V1 (manual, Codespaces):"
	@echo "    make setup doctor preflight backend model-v1 plan-v1 deploy-v1 status-v1 smoke-v1 benchmark-v1 quality"
	@echo "  Trilha V2 (pipeline, runner efemero):"
	@echo "    make runner-plan runner-apply runner-register-help   # depois: git push, workflow 04-2-deploy-v2.yml roda no runner"
	@echo "  Fechamento: make evidence destroy-models runner-destroy destroy-all verify-clean"
	@echo "  Ciclo local completo (so V1 + infra + qualidade, nunca V2): make e2e"

setup: ## Cria/atualiza o .venv do lab com as dependencias fixadas
	@bash $(SCRIPTS)/setup.sh

doctor: setup ## Portao obrigatorio: ferramentas, credencial/regiao/role, disco, conectividade
	@$(PYTHON) $(SCRIPTS)/doctor.py > /dev/null

# --------------------------------------------------------------------------- #
# Trilha V1 - manual, executada de dentro do Codespaces com credencial do
# Academy colada em ~/.aws/credentials.
# --------------------------------------------------------------------------- #

preflight: doctor ## Mede runtime real (DLC, contexto, latencia) contra o Academy - dono: A2/A8
	@$(PYTHON) $(SCRIPTS)/resolve_runtime.py

backend: doctor ## Cria bucket de state (terraform_data+local-exec) e inicializa terraform/slm - dono: A6
	@$(TF) -chdir=terraform/slm init -input=false

model-v1: doctor ## Baixa/quantiza (Q4_0) e publica a release V1 no bucket de artifacts - dono: A7
	@$(PYTHON) $(SCRIPTS)/model_sync.py --release v1

model-v2: doctor ## Baixa/quantiza (Q4_K_M) e publica a release V2 no bucket de artifacts - dono: A7
	@$(PYTHON) $(SCRIPTS)/model_sync.py --release v2

plan-v1: backend model-v1 ## Planeja o endpoint V1 em terraform/slm - dono: A6
	@$(TF) -chdir=terraform/slm plan -input=false -var release=v1

deploy-v1: backend model-v1 ## Aplica o endpoint V1 (fixed_capacity=1) + dashboard - dono: A6
	@$(TF) -chdir=terraform/slm apply -input=false -auto-approve -var release=v1
	@$(TF) -chdir=terraform/slm output

status-v1: doctor ## Status do endpoint V1 em JSON (nome, EndpointStatus, instance type) - dono: A6
	@$(PYTHON) $(SCRIPTS)/runtime_status.py --release v1

smoke-v1: doctor ## Roda os casos de fumaca contra o endpoint V1 - dono: A8
	@$(PYTHON) $(SCRIPTS)/invoke.py --release v1 --smoke

benchmark-v1: doctor ## Mede latencia/throughput real do V1 (base do target_value de autoscaling) - dono: A8
	@$(PYTHON) $(SCRIPTS)/benchmark.py --release v1

quality: doctor ## Roda a avaliacao generativa (nunca exact-match) contra o V1 - dono: A8
	@$(PYTHON) $(SCRIPTS)/evaluate.py --release v1

# --------------------------------------------------------------------------- #
# Trilha V2 - runner self-hosted efemero (terraform/runner, dono: A5). O
# registro do runner no GitHub exige colar um token gerado na hora pela UI do
# GitHub (Settings -> Actions -> Runners -> New self-hosted runner); nenhum
# PAT e inventado ou armazenado para automatizar esse passo (blueprint S19).
# --------------------------------------------------------------------------- #

runner-plan: doctor ## Planeja a EC2 efemera do runner (t3.medium, IMDSv2 hop-limit 1) - dono: A5
	@$(TF) -chdir=terraform/runner init -input=false
	@$(TF) -chdir=terraform/runner plan -input=false

runner-apply: doctor ## Provisiona a EC2 do runner - dono: A5
	@$(TF) -chdir=terraform/runner apply -input=false -auto-approve
	@$(TF) -chdir=terraform/runner output

runner-status: doctor ## Consulta se o runner esta registrado e online no GitHub - dono: A5
	@$(PYTHON) $(SCRIPTS)/runner_status.py

runner-register-help: ## Imprime o passo manual exato para registrar o runner no GitHub (token via UI)
	@bash $(SCRIPTS)/register_runner.sh --help

pipeline-preflight: ## Prova, dentro do workflow, que a credencial do runner e instance profile (nunca chave estatica) - dono: A5/A8
	@$(PYTHON) $(SCRIPTS)/resolve_runtime.py --credential-source-only

autoscaling-status: doctor ## Le o scalable target/policy do V2 e grava artifacts/evidence/autoscaling.json - dono: A6
	@$(PYTHON) $(SCRIPTS)/runtime_status.py --autoscaling

compare: doctor ## Compara V1 x V2 (latencia, qualidade, custo) e recomenda a release - dono: A8
	@$(PYTHON) $(SCRIPTS)/evaluate.py --compare

# --------------------------------------------------------------------------- #
# Evidencia, limpeza e verificacao - minha responsabilidade direta (A0)
# --------------------------------------------------------------------------- #

evidence: doctor ## Consolida o dossie conferivel em artifacts/evidence/ (manifest.json + evidence.md)
	@$(PYTHON) $(SCRIPTS)/evidence.py > /dev/null

destroy-models: doctor ## Destroi endpoints/configs/models do stack terraform/slm (V1 e V2)
	@$(TF) -chdir=terraform/slm destroy -input=false -auto-approve

runner-destroy: doctor ## Destroi a EC2 efemera do runner (terraform/runner) - roda sempre a partir do Codespaces
	@$(TF) -chdir=terraform/runner destroy -input=false -auto-approve

destroy-all: destroy-models runner-destroy ## Limpeza completa (modelos + runner), sempre disparada do Codespaces
	@echo "== destroy-all concluido; confirme com 'make verify-clean' =="

verify-clean: doctor ## Prova por consulta direta a API que nao sobrou nada cobrando
	@$(PYTHON) $(SCRIPTS)/verify_clean.py

e2e: ## Ciclo local completo: V1 + infra + qualidade (nunca registra o runner nem dispara V2)
	@set -euo pipefail; \
	if [ "$${KEEP_RESOURCES:-0}" = "1" ]; then \
		echo "!! KEEP_RESOURCES=1: o endpoint V1 real-time vai continuar de pe e continuar cobrando."; \
		echo "!! Rode 'make destroy-models' assim que terminar, ou o credito do lab paga por ele."; \
	else \
		trap 'code=$$?; echo "== limpeza (roda sempre) =="; \
			$(MAKE) --no-print-directory destroy-models || true; \
			$(MAKE) --no-print-directory verify-clean || true; exit $$code' EXIT; \
	fi; \
	$(MAKE) --no-print-directory doctor; \
	$(MAKE) --no-print-directory preflight; \
	$(MAKE) --no-print-directory backend; \
	$(MAKE) --no-print-directory model-v1; \
	$(MAKE) --no-print-directory plan-v1; \
	$(MAKE) --no-print-directory deploy-v1; \
	$(MAKE) --no-print-directory status-v1; \
	$(MAKE) --no-print-directory smoke-v1; \
	$(MAKE) --no-print-directory benchmark-v1; \
	$(MAKE) --no-print-directory quality; \
	$(MAKE) --no-print-directory evidence; \
	echo "== e2e local terminou aqui. V2 exige um runner self-hosted registrado"; \
	echo "   manualmente (make runner-plan / runner-apply / runner-register-help) e"; \
	echo "   um 'git push' que dispare .github/workflows/04-2-deploy-v2.yml =="

clean: ## Remove artefatos locais gerados (nunca toca na AWS)
	@rm -rf $(ARTIFACTS) $(GENERATED)
	@find . -name __pycache__ -type d -prune -exec rm -rf {} +
