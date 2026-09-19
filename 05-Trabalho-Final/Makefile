# Trabalho Final - Cloud-Based Machine Learning (Bora Fibra)
#
# Caminho principal do aluno, sempre nesta ordem:
#   make bootstrap -> editar student/solution.yaml e student/DECISION.md ->
#   make deploy -> make compare -> make run (repetível após reeditar o YAML) ->
#   make finish.
# `make run` nunca reprovisiona: nenhuma dependência sua chama apply/train.
# O aluno nunca roda terraform direto - todo estágio passa por este Makefile.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# .venv/bin/python vence o python3 do PATH: um shell de login recarrega o
# profile e derrubaria o .venv na frente, rodando o CLI num interpretador sem
# boto3/PyYAML instalados.
PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
TF ?= terraform
TF_DIR := terraform
SCRIPTS := scripts
ARTIFACTS ?= artifacts
BUILD ?= .generated

export PYTHONPATH := src
# Exportado para scripts/final.py e o Makefile usarem sempre o mesmo binário
# de Terraform ao ler outputs do state - numa máquina com duas versões
# instaladas, ler com uma e aplicar com outra corrompe o handoff do artifact.
export TF

# O Academy só autoriza us-east-1: fixado aqui para nenhuma sessão herdar uma
# região diferente do ambiente. AWS_PROFILE nunca é definido por este
# Makefile - o Academy não tem perfil [default], então a credencial (perfil
# testeAIML) precisa já estar exportada no shell do aluno antes do `make`.
export AWS_REGION := us-east-1
export AWS_DEFAULT_REGION := us-east-1

# student_id nomeia todo recurso AWS (local.prefix) e precisa ser estável
# entre chamadas de make - senão cada apply criaria recursos com nomes novos
# e órfãos ficariam cobrando. scripts/setup.sh gera e persiste esse arquivo
# na primeira execução; aqui só lemos.
STUDENT_ID_FILE := $(BUILD)/student_id
export TF_VAR_student_id := $(shell cat $(STUDENT_ID_FILE) 2>/dev/null || echo aluno)

# Handoff do artifact de treino para o estágio 2 do apply - o CONTRATO proíbe
# montar essa URI por convenção; só existe depois que `make deploy` chama o
# subcomando `artifact`, que a confirma via DescribeTrainingJob + HeadObject.
HANDOFF := $(BUILD)/artifact.auto.tfvars.json

.PHONY: help bootstrap deploy compare run finish \
        setup doctor student-files data validate-data validate-solution \
        train training-status artifact atendimento campanha baseline drift \
        alarm-status reaction ground-truth dashboard evidence resumo check \
        verify-clean package validate-package \
        test fmt validate apply status destroy clean

help: ## Lista os alvos disponíveis
	@echo "Trabalho Final - Cloud-Based Machine Learning (Bora Fibra)"
	@echo
	@echo "Caminho principal (nesta ordem):"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ★' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## ★ "}; {printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Entre 'make deploy' e 'make run', edite student/solution.yaml (os dois"
	@echo "  patterns de serving) e student/DECISION.md. Repita 'make run' à vontade"
	@echo "  depois de reeditar o YAML - não reprovisiona nada."
	@echo
	@echo "Apoio e diagnóstico (o golden path acima já chama estes internamente):"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| grep -v '## ★' \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------------- #
# Golden path - os cinco comandos que o aluno precisa saber de cor.
# --------------------------------------------------------------------------- #

bootstrap: ## ★ 1/5 · Prepara venv, credencial, dataset e Terraform validado (5-8 min, sem custo)
	@$(MAKE) --no-print-directory setup
	@$(MAKE) --no-print-directory doctor
	@$(MAKE) --no-print-directory student-files
	@$(MAKE) --no-print-directory data
	@$(MAKE) --no-print-directory validate-data
	@$(MAKE) --no-print-directory test
	@$(MAKE) --no-print-directory validate
	@echo
	@echo "== bootstrap concluído =="
	@echo "Próximo passo: edite student/solution.yaml e student/DECISION.md, depois rode 'make deploy'."

deploy: validate-data validate ## ★ 2/5 · Sobe treino, os candidatos de serving e a observabilidade (15-20 min)
	@echo "== estágio 1/2: storage, dataset e training job (enable_serving=false) =="
	@mkdir -p $(BUILD)
	@rm -f $(HANDOFF)
	@$(TF) -chdir=$(TF_DIR) apply -input=false -auto-approve -var enable_serving=false
	@$(MAKE) --no-print-directory train
	@echo "== portão: esperando o training job atingir um estado terminal =="
	@$(PYTHON) $(SCRIPTS)/final.py training-status --wait
	@$(MAKE) --no-print-directory artifact
	@echo "== estágio 2/2: model, os três candidatos de serving e a observabilidade =="
	@$(TF) -chdir=$(TF_DIR) apply -input=false -auto-approve -var enable_serving=true -var-file=$(HANDOFF)
	@$(TF) -chdir=$(TF_DIR) output
	@echo
	@echo "== deploy concluído =="
	@echo "Próximo passo: 'make compare' e depois escolha os patterns em student/solution.yaml."

compare: ## ★ 3/5 · Gera evidência comparável dos quatro candidatos, sem alterar infraestrutura
	@$(PYTHON) $(SCRIPTS)/final.py compare

run: ## ★ 4/5 · Roda inferência e operação com os patterns do student/solution.yaml (12-18 min)
	@$(MAKE) --no-print-directory validate-solution
	@$(MAKE) --no-print-directory atendimento
	@$(MAKE) --no-print-directory campanha
	@$(MAKE) --no-print-directory baseline
	@$(MAKE) --no-print-directory drift
	@$(MAKE) --no-print-directory alarm-status
	@$(MAKE) --no-print-directory reaction
	@$(MAKE) --no-print-directory ground-truth
	@$(MAKE) --no-print-directory evidence
	@$(MAKE) --no-print-directory resumo

finish: check destroy verify-clean ## ★ 5/5 · Desliga tudo, prova a limpeza e empacota a entrega (8-10 min)
	@$(MAKE) --no-print-directory evidence
	@$(MAKE) --no-print-directory package
	@$(MAKE) --no-print-directory validate-package

# --------------------------------------------------------------------------- #
# Apoio - venv, credencial e arquivos do aluno.
# --------------------------------------------------------------------------- #

setup: ## Cria/atualiza o .venv com as dependências fixadas
	@bash $(SCRIPTS)/setup.sh

doctor: setup ## Confere ferramentas, credencial/região e alcance dos serviços AWS
	@$(PYTHON) $(SCRIPTS)/final.py doctor

# Sem alvo de template separado: student/solution.yaml e student/DECISION.md
# já chegam no repositório como os próprios templates (dono: outro agente do
# trabalho). Este alvo nunca sobrescreve - só impede seguir em frente se um
# dos dois tiver sido apagado, quando a correção é `git checkout`, não algo
# que o Makefile possa inventar.
student-files:
	@for f in student/solution.yaml student/DECISION.md; do \
		if [ ! -f "$$f" ]; then \
			echo "erro: $$f não existe. Restaure com 'git checkout -- $$f' antes de continuar." >&2; \
			exit 1; \
		fi; \
	done

# --------------------------------------------------------------------------- #
# Subcomandos do CLI (scripts/final.py) expostos como alvos individuais -
# existem para diagnóstico pontual; o golden path acima já os encadeia.
# --------------------------------------------------------------------------- #

data: setup ## Gera o dataset determinístico e as janelas de produção
	@$(PYTHON) $(SCRIPTS)/final.py data

validate-data: data ## Roda o contrato de dados executável e imprime os hashes
	@$(PYTHON) $(SCRIPTS)/final.py validate-data

validate-solution: ## Valida os dois patterns escolhidos em student/solution.yaml
	@$(PYTHON) $(SCRIPTS)/final.py validate-solution

train: ## Submete o training job (não espera - use 'training-status')
	@$(PYTHON) $(SCRIPTS)/final.py train

training-status: ## Mostra o estado atual do training job
	@$(PYTHON) $(SCRIPTS)/final.py training-status

artifact: ## Confirma o artefato treinado via API e grava o handoff do apply estágio 2
	@$(PYTHON) $(SCRIPTS)/final.py artifact

atendimento: ## Roda a carga de atendimento (1 cliente por vez) no pattern escolhido
	@$(PYTHON) $(SCRIPTS)/final.py atendimento

campanha: ## Roda a carga de campanha (lote) no pattern escolhido
	@$(PYTHON) $(SCRIPTS)/final.py campanha

baseline: ## Observa a janela saudável e publica as métricas de referência
	@$(PYTHON) $(SCRIPTS)/final.py baseline

drift: ## Observa a janela deslocada e publica o drift medido
	@$(PYTHON) $(SCRIPTS)/final.py drift

alarm-status: ## Mostra o estado dos alarmes de drift
	@$(PYTHON) $(SCRIPTS)/final.py alarm-status

reaction: ## Espera e valida o incidente que a Lambda escreve no S3
	@$(PYTHON) $(SCRIPTS)/final.py reaction

ground-truth: ## Avalia a qualidade com o rótulo que chegou depois (F1/ROC-AUC)
	@$(PYTHON) $(SCRIPTS)/final.py ground-truth

dashboard: ## Imprime o nome e o link direto do dashboard do CloudWatch
	@$(PYTHON) $(SCRIPTS)/final.py dashboard

evidence: ## Consolida o dossiê conferível em artifacts/evidence/
	@$(PYTHON) $(SCRIPTS)/final.py evidence

resumo: ## Grava a tabela de evidências em student/DECISION.md e repete no terminal
	@$(PYTHON) $(SCRIPTS)/final.py resumo

check: ## Confere que a evidência mínima existe antes de destruir a infraestrutura
	@$(PYTHON) $(SCRIPTS)/final.py check

verify-clean: ## Prova por consulta direta à API que não sobrou nada cobrando
	@$(PYTHON) $(SCRIPTS)/final.py verify-clean

package: ## Empacota a entrega final (código + evidência) num zip
	@$(PYTHON) $(SCRIPTS)/final.py package

validate-package: ## Confere a integridade do zip empacotado
	@$(PYTHON) $(SCRIPTS)/final.py validate-package

# --------------------------------------------------------------------------- #
# Terraform direto - só estes quatro alvos chamam terraform, e só para
# diagnóstico avançado. O aluno não precisa deles: 'make deploy'/'make finish'
# já orquestram o apply/destroy nos momentos certos.
# --------------------------------------------------------------------------- #

test: setup ## Roda os testes unitários (contrato de dados, drift, empacotamento)
	@$(PYTHON) -m pytest -q tests

fmt: ## Formata o Terraform (confere no CI, reescreve localmente)
	@$(TF) fmt -recursive $(TF_DIR)

validate: ## terraform init + fmt -check + validate
	@$(TF) -chdir=$(TF_DIR) init -input=false -upgrade=false
	@$(TF) -chdir=$(TF_DIR) fmt -check -recursive
	@$(TF) -chdir=$(TF_DIR) validate

apply: validate ## (avançado) terraform apply cru do estágio atual - normalmente use 'make deploy'
	@$(TF) -chdir=$(TF_DIR) apply -input=false -auto-approve

status: ## Mostra os outputs atuais do Terraform (bucket, endpoints, dashboard)
	@$(TF) -chdir=$(TF_DIR) output

destroy: ## Destrói todos os recursos geridos pelo Terraform
	@$(TF) -chdir=$(TF_DIR) destroy -input=false -auto-approve
	@rm -f $(HANDOFF)

clean: ## Remove artefatos locais gerados (nunca toca na AWS)
	@rm -rf $(ARTIFACTS) $(BUILD)
	@find . -name __pycache__ -type d -prune -exec rm -rf {} +
