# Lab 04.2 - Do score a acao: SLM, releases e CI/CD sem chaves AWS
#
# Duas trilhas de credencial, um unico contrato de nomes/targets:
#   V1 (manual, Codespaces)  -> setup -> doctor -> preflight -> backend ->
#     model-v1 -> plan-v1 -> deploy-v1 -> status-v1 -> smoke-v1 ->
#     benchmark-v1 -> quality
#   V2 (pipeline, runner efemero) -> runner-token -> runner-plan -> runner-apply ->
#     (registro automatico no boot, decisao D44; runner-register-help mostra o
#     fallback manual) -> (push dispara .github/workflows/04-2-deploy-v2.yml
#     no runner self-hosted, sem chave AWS estatica no GitHub) -> compare ->
#     dashboard -> resumo
# Fechamento: evidence -> destroy-models -> runner-destroy -> destroy-all ->
# verify-clean. `make e2e` roda o que e 100% Codespaces (V1 + infra + qualidade
# local); nunca inventa um PAT para automatizar o registro do runner.
#
# `dashboard` e `resumo` nunca mutam a AWS nem o Terraform: o primeiro so le
# (GetDashboard); o segundo so le artifacts/evidence/*.json locais e regrava a
# tabela do DECISION.md. Podem ser chamados a qualquer momento do lab.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
TF ?= terraform
SCRIPTS := scripts
ARTIFACTS ?= artifacts
GENERATED ?= .generated

# Precisa ficar igual ao default de var.runner_token_ssm_parameter em
# terraform/runner/variables.tf (decisão D44) — nome do parâmetro, nunca o
# valor. Só é lido aqui para saber ONDE gravar; o valor nunca entra em
# variável de make (apareceria em 'ps'/histórico), só em variável de shell
# lida com `read -rs` dentro do próprio alvo.
RUNNER_TOKEN_SSM_PARAMETER ?= /fiap-cbml-42/runner-registration-token
GITHUB_REPO_URL := https://github.com/vamperst/FIAP-Cloud-Based-Machine-Learning

export PYTHONPATH := src
# Mesma razao do 04.1: um so binario de terraform para make, scripts Python e
# para quem le os outputs do state (src/lab42/aws.py:terraform_outputs).
export TF

.PHONY: help setup doctor preflight backend model-v1 model-v2 \
        runner-plan runner-apply runner-token runner-status runner-register-help \
        plan-v1 deploy-v1 status-v1 smoke-v1 benchmark-v1 quality \
        pipeline-preflight autoscaling-status compare dashboard resumo evidence \
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
	@echo "    make runner-token runner-plan runner-apply   # depois: git push, workflow 04-2-deploy-v2.yml roda no runner"
	@echo "    (runner-token grava o token no SSM antes do apply - registro automatico no boot, decisao D44;"
	@echo "     make runner-register-help mostra o caminho automatico e o manual/fallback)"
	@echo "  Fechamento: make evidence destroy-models runner-destroy destroy-all verify-clean"
	@echo "  Ciclo local completo (so V1 + infra + qualidade, nunca V2): make e2e"
	@echo "  Painel e DECISION.md (nunca tocam a AWS alem de GetDashboard): make dashboard resumo"

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
	@$(PYTHON) $(SCRIPTS)/bootstrap_backend.py
	@$(TF) -chdir=terraform/slm init -input=false -reconfigure -backend-config=../../.generated/backend.hcl

model-v1: doctor ## Baixa/quantiza (Q4_0) e publica a release V1 no bucket de artifacts - dono: A7
	@$(PYTHON) $(SCRIPTS)/model_sync.py --release v1

model-v2: doctor ## Baixa/quantiza (Q4_K_M) e publica a release V2 no bucket de artifacts - dono: A7
	@$(PYTHON) $(SCRIPTS)/model_sync.py --release v2

plan-v1: backend model-v1 ## Planeja o endpoint V1 em terraform/slm - dono: A6
	@$(TF) -chdir=terraform/slm plan -input=false -var release=v1

deploy-v1: backend ## Aplica o endpoint V1 (fixed_capacity=1) + dashboard - dono: A6
	@echo "== estagio 1/2: bucket de artefatos (o upload do modelo precisa dele antes de existir) =="
	@$(TF) -chdir=terraform/slm apply -input=false -auto-approve -target=terraform_data.artifacts_bucket -var release=v1
	@echo "== publicando a release v1 no bucket =="
	@$(MAKE) --no-print-directory model-v1
	@echo "== estagio 2/2: model, endpoint e dashboard =="
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
# registro do runner no GitHub exige um token de curta duracao gerado na hora
# pela UI do GitHub (Settings -> Actions -> Runners -> New self-hosted
# runner); nenhum PAT (token de longa duracao) e inventado ou armazenado para
# automatizar esse passo (blueprint S19). `runner-token` grava esse token de
# curta duracao como SecureString no SSM Parameter Store so para o boot da
# proxima instancia consumir e apagar sozinho (decisao D44) - nunca fica
# guardado depois do registro, e o valor nunca passa pelo Terraform.
# --------------------------------------------------------------------------- #

runner-plan: doctor ## Planeja a EC2 efemera do runner (t3.medium, IMDSv2 hop-limit 1) - dono: A5
	@$(TF) -chdir=terraform/runner init -input=false
	@$(TF) -chdir=terraform/runner plan -input=false

runner-apply: doctor ## Provisiona a EC2 do runner - dono: A5
	@$(TF) -chdir=terraform/runner apply -input=false -auto-approve
	@$(TF) -chdir=terraform/runner output

runner-token: doctor ## Grava o token de registro no SSM (SecureString) - roda ANTES de runner-apply p/ registro automatico (decisao D44)
	@echo "===============================================================" >&2; \
	echo "Cole o token de registro do GitHub:" >&2; \
	echo "  Settings -> Actions -> Runners -> New self-hosted runner" >&2; \
	echo "  ($(GITHUB_REPO_URL)/settings/actions/runners/new)" >&2; \
	echo "O token nao aparece na tela, nao fica em historico e nunca e" >&2; \
	echo "argumento deste make (isso apareceria em 'ps'/historico do shell)." >&2; \
	echo "Ele expira em poucos minutos - copie e cole aqui na sequencia." >&2; \
	echo "===============================================================" >&2; \
	read -rs runner_token; \
	echo >&2; \
	if [ -z "$${runner_token:-}" ]; then \
		echo "Erro: token vazio, nada foi gravado." >&2; \
		exit 1; \
	fi; \
	AWS_PAGER="" aws ssm put-parameter \
		--name "$(RUNNER_TOKEN_SSM_PARAMETER)" \
		--type SecureString \
		--value "$$runner_token" \
		--overwrite >/dev/null; \
	unset runner_token; \
	echo "Token gravado em $(RUNNER_TOKEN_SSM_PARAMETER) (SecureString)." >&2; \
	echo "Rode 'make runner-apply' a seguir: o user-data le e apaga este parametro no boot." >&2

runner-status: doctor ## Consulta se o runner esta registrado e online no GitHub - dono: A5
	@$(PYTHON) $(SCRIPTS)/runner_status.py

runner-register-help: ## Imprime os dois caminhos de registro: automatico (SSM, default) e manual/fallback (token via UI)
	@echo "===============================================================" >&2
	@echo "Caminho automatico (default, decisao D44 - sem sessao SSM/login GitHub):" >&2
	@echo "  1. make runner-token   # cola o token, grava SecureString no SSM" >&2
	@echo "  2. make runner-apply   # user-data le o parametro no boot," >&2
	@echo "                         # registra o runner e apaga o parametro sozinho" >&2
	@echo "  Confira: terraform -chdir=terraform/runner output runner_token_ssm_check_command" >&2
	@echo >&2
	@echo "Caminho manual/fallback (se o parametro nao existia ou o registro" >&2
	@echo "automatico falhou - ver /var/log/user-data.log na instancia):" >&2
	@bash $(SCRIPTS)/register_runner.sh --help

pipeline-preflight: ## Prova, dentro do workflow, que a credencial do runner e instance profile (nunca chave estatica) - dono: A5/A8
	@$(PYTHON) $(SCRIPTS)/resolve_runtime.py --credential-source-only

autoscaling-status: doctor ## Le o scalable target/policy do V2 e grava artifacts/evidence/autoscaling.json - dono: A6
	@$(PYTHON) $(SCRIPTS)/runtime_status.py --autoscaling

compare: doctor ## Compara V1 x V2 (latencia, qualidade, custo) e recomenda a release - dono: A8
	@$(PYTHON) $(SCRIPTS)/evaluate.py --compare

dashboard: ## Imprime o link do painel do CloudWatch, confirmado por GetDashboard antes (nunca entrega link 404)
	@$(PYTHON) $(SCRIPTS)/dashboard.py

resumo: ## Regrava a tabela de evidencias do DECISION.md com os numeros medidos e imprime as mesmas frases - nunca toca a AWS
	@$(PYTHON) $(SCRIPTS)/resumo.py >/dev/null

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
	echo "== e2e local terminou aqui. V2 exige um runner self-hosted (make runner-token"; \
	echo "   / runner-plan / runner-apply - registro automatico no boot, decisao D44) e"; \
	echo "   um 'git push' que dispare .github/workflows/04-2-deploy-v2.yml =="

clean: ## Remove artefatos locais gerados (nunca toca na AWS)
	@rm -rf $(ARTIFACTS) $(GENERATED)
	@find . -name __pycache__ -type d -prune -exec rm -rf {} +
