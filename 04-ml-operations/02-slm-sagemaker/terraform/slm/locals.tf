# Fundação de identidade e nomenclatura do stack terraform/slm.
#
# account_id/suffix aqui precisam bater byte a byte com
# src/lab42/aws.py:account_id()/suffix() — os dois lados resolvem contra a
# MESMA conta (STS) e o MESMO owner do GitHub, então o Terraform delega a
# leitura do owner para o próprio ambiente (env var no CI, git remote no
# Codespaces) em vez de reimplementar em HCL um parser que pudesse divergir
# do Python com o tempo. account_id vem de um data source nativo do provider
# AWS: a mesma chamada STS que aws.account_id() faz.
data "aws_caller_identity" "current" {}

# Nome "lab_role_a6a" (não "lab_role") de propósito: sagemaker.tf (A6b) já
# declara seu próprio `data "aws_iam_role" "lab_role"` no domínio dele, e um
# módulo Terraform não aceita dois data sources com o mesmo endereço — nomes
# distintos evitam a colisão sem exigir que nenhum dos dois arquivos dependa
# do outro para resolver o role.
data "aws_iam_role" "lab_role_a6a" {
  name = var.execution_role_name
}

# Replica em shell puro (sem depender de boto3/pyyaml no PATH em que o
# `terraform apply` roda) a MESMA ordem de resolução de
# src/lab42/aws.py:github_owner(): 1) GITHUB_REPOSITORY_OWNER (presente em
# qualquer job do GitHub Actions, inclusive o runner self-hosted efêmero do
# deploy V2); 2) `git config --get remote.origin.url` do checkout, parseando
# as duas formas usuais (`git@github.com:owner/repo.git` e
# `https://github.com/owner/repo.git`). Nunca cai para nome de aluno — falha
# explícita (exit 1) se nenhuma das duas fontes existir, e o `terraform plan`
# reprova com a mensagem de erro deste script, nunca com um nome inventado.
data "external" "github_owner" {
  program = [
    "bash",
    "-c",
    <<-EOT
      set -eo pipefail
      owner="$GITHUB_REPOSITORY_OWNER"
      if [ -z "$owner" ]; then
        url=$(git config --get remote.origin.url 2>/dev/null || true)
        if [ -z "$url" ]; then
          echo "nem GITHUB_REPOSITORY_OWNER nem remote.origin.url disponivel; o sufixo determinístico depende de um dos dois" >&2
          exit 1
        fi
        cleaned=$(printf '%s' "$url" | sed -E 's#\.git$##' | sed -E 's#/$##')
        case "$cleaned" in
          git@*) owner=$(printf '%s' "$cleaned" | sed -E 's#.*:([^/]+)/.*#\1#') ;;
          *) owner=$(printf '%s' "$cleaned" | sed -E 's#.*/([^/]+)/[^/]+$#\1#') ;;
        esac
      fi
      if [ -z "$owner" ]; then
        echo "não foi possível extrair o owner de remote.origin.url: $url" >&2
        exit 1
      fi
      printf '{"owner":"%s"}' "$owner"
    EOT
  ]
}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  github_owner = data.external.github_owner.result.owner

  # Fórmula única (config/lab.yaml: naming.suffix_algorithm; espelha
  # src/lab42/aws.py:suffix()): sha256(account_id:github_owner), 8 primeiros
  # caracteres em hex minúsculo. sha256()/substr() do Terraform já produzem
  # hex minúsculo, igual a hashlib.sha256(...).hexdigest() em Python — as
  # duas pontas calculam o MESMO valor sem se falar.
  suffix = substr(sha256("${local.account_id}:${local.github_owner}"), 0, 8)

  # Prefixo semântico de todo recurso do lab (config/lab.yaml: aws.bucket_prefix).
  project_prefix = "fiap-cbml-42"

  bucket_state_name     = "${local.project_prefix}-tfstate-${local.account_id}-${local.suffix}"
  bucket_artifacts_name = "${local.project_prefix}-models-${local.account_id}-${local.suffix}"

  # D20 (decisão do coordenador, pós-colisão de 10 locals em dashboard.tf):
  # este locals.tf carrega só a FUNDAÇÃO (identidade, nomenclatura de bucket,
  # role, tags). Nomes de endpoint (endpoint_v1_name/v2_name/endpoint_name_
  # current) e qualquer outro local específico de SageMaker/autoscaling/
  # dashboard agora vivem em locals_slm.tf, de propriedade do A6b — que pode
  # referenciar local.project_prefix/local.suffix daqui livremente, já que
  # locals são escopados ao módulo inteiro, não ao arquivo.
  execution_role_arn = data.aws_iam_role.lab_role_a6a.arn

  # Layout D7 (decisão de integração 21:29, obrigatória para A6b e A7): um
  # prefixo de S3 por release, com exatamente um `.gguf` dentro — nunca
  # misturar Q4_0/Q4_K_M no mesmo prefixo, porque `ModelDataSource` com
  # S3DataType=S3Prefix sincroniza o prefixo INTEIRO para /opt/ml/model.
  # `models_prefix_base` é a raiz que A6b/A7 combinam com
  # `<revision>/<release>/` (revision/release vêm do manifest
  # model/releases/v{release}.yaml, dono A7) — nunca hardcodar o nome do
  # repositório HF fora deste local, para o dia em que o modelo mudar.
  model_repo_dirname = "Qwen2.5-0.5B-Instruct-GGUF"
  models_prefix_base = "models/${local.model_repo_dirname}"

  # Nenhum dado pessoal nas tags — elas caem em relatório de custo visível
  # para a turma toda (mesma disciplina do Lab 04.1). `model_lineage` deste
  # lab é o SLM de retenção (bora-retention-slm), separado da linhagem do
  # churn (upstream_model_lineage), para as duas serem auditáveis por tag de
  # forma independente — ver config/lab.yaml (lineage).
  tags = {
    course                 = "cloud-based-machine-learning"
    lab                    = "04-2-slm-sagemaker"
    business_capability    = "bora-fibra-retention-copilot"
    model_lineage          = "bora-retention-slm"
    upstream_model_lineage = "churn-v1"
    purpose                = "education"
    managed_by             = "terraform"
  }

  # Mesmo conjunto de tags, no formato que a API do S3 espera — para o
  # bucket que a CLI cria em s3.tf, já que o default_tags do provider só
  # alcança recursos que o próprio provider cria (não um `terraform_data`
  # com local-exec).
  bucket_tagging_json = jsonencode({
    TagSet = [for k, v in local.tags : { Key = k, Value = v }]
  })
}
