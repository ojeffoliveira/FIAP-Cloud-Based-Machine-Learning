# Modelos, endpoint configurations e endpoints do SLM de retenção (Bora Fibra).
#
# CONTRATO COM A6a (fundação, dono de locals.tf/s3.tf/variables.tf/providers.tf
# neste mesmo diretório — não editar os arquivos dele, só consumir). Locals
# consumidos daquele arquivo, conferidos linha a linha antes de usar aqui:
#   local.suffix, local.project_prefix, local.execution_role_arn,
#   local.bucket_artifacts_name, local.models_prefix_base,
#   local.endpoint_v1_name, local.endpoint_v2_name, local.tags (via
#   default_tags do provider — não precisa referenciar direto).
# `terraform_data.artifacts_bucket` (recurso dele, s3.tf) é referenciado só
# para criar a dependência de ordem (bucket antes do model), nunca reescrito.
#
# IMPORTANTE — divergência encontrada com var.release (variables.tf do A6a):
# aquele arquivo descreve um único release ativo por apply ("nunca as duas
# releases no ar ao mesmo tempo"). Isso contradiria a missão (V1+V2
# coexistindo, quota confirmada de 4x ml.m5.xlarge, D11) e a regra "não
# destruir o V1". Resolução adotada aqui: os recursos deste arquivo NÃO usam
# var.release para count — usam var.enable_v1/var.enable_v2 (declaradas
# abaixo), que são independentes uma da outra e por isso provam o faseamento
# sem destruir nada. var.release fica livre para outros consumidores
# (Makefile/scripts) que queiram "qual é o release corrente" sem afetar a
# coexistência dos dois endpoints. Registrado no handoff para o coordenador
# resolver a ambiguidade entre os dois modelos mentais.
#
# Nomes de bucket/endpoint/dashboard seguem config/lab.yaml (fonte única de
# verdade de nomenclatura do lab), nunca inventados aqui.

# Faseamento obrigatório (missão A6b, decisão D11): o apply do Codespaces sobe só
# V1; o apply do runner (CI, deploy V2) liga V2 sem tocar V1. Cada versão tem seu
# próprio bloco de recursos gated por uma variável independente — nunca um único
# recurso com count dependente da combinação das duas flags. Assim, ligar
# enable_v2 nunca aparece como diff em nenhum recurso de V1 no `terraform plan`.
variable "enable_v1" {
  description = "Habilita o release V1 (Q4_0, capacidade fixa) do endpoint SLM. Default true: é o baseline que sobe no Codespaces."
  type        = bool
  default     = true
}

variable "enable_v2" {
  description = "Habilita o release V2 (Q4_K_M, com autoscaling) do endpoint SLM. Default false: só o workflow de deploy V2 (runner self-hosted) liga isso, depois que V1 já existe."
  type        = bool
  default     = false
}

locals {
  # ml.m5.xlarge é o único tipo aprovado nos guardrails do Academy para este lab
  # (4 vCPU / 16 GiB, sem GPU) — quota confirmada de 4 unidades para
  # "ml.m5.xlarge for endpoint usage", suficiente para V1 + V2 coexistirem.
  sagemaker_instance_type = "ml.m5.xlarge"

  # Imagem do DLC llama.cpp CPU, provada pelo A2 (runtime.json) — único caminho,
  # fallback PyTorch descartado pela D1. Repetida aqui (não em locals.tf do A6a)
  # porque é um fato do domínio SageMaker, não da fundação/backend.
  llamacpp_image_uri = "763104351884.dkr.ecr.us-east-1.amazonaws.com/huggingface-llamacpp:b9522-cpu-ubuntu24.04"

  # Contexto validado no probe real (LLAMA_ARG_CTX_SIZE=2048 -> "n_ctx = 2048" no
  # log do container). LLAMA_ARG_HTTP_THREADS é ignorada silenciosamente pelo
  # upstream (nome real é LLAMA_ARG_THREADS_HTTP) — não configurado de propósito,
  # o default de 8 threads HTTP já é adequado para o volume do lab (D1/runtime.json).
  llamacpp_ctx_size = "2048"

  # Um prefixo S3 por release, com exatamente um .gguf (D7) — evita que
  # ModelDataSource (S3Prefix) sincronize os dois arquivos (~920 MB) para o
  # mesmo container. Revisão pinada, nunca "main" (ver model/releases/v1.yaml e
  # v2.yaml, dono A7 — valores replicados aqui só como string literal, não como
  # referência cruzada de arquivo, porque model/**  é domínio do A7).
  releases = {
    v1 = {
      revision     = "9217f5db79a29953eb74d5343926648285ec7e67"
      filename     = "qwen2.5-0.5b-instruct-q4_0.gguf"
      variant_name = "AllTraffic"
    }
    v2 = {
      revision     = "9217f5db79a29953eb74d5343926648285ec7e67"
      filename     = "qwen2.5-0.5b-instruct-q4_k_m.gguf"
      variant_name = "AllTraffic"
    }
  }

  # `local.models_prefix_base` (locals.tf do A6a) já é a raiz
  # "models/Qwen2.5-0.5B-Instruct-GGUF" — combinada aqui com
  # `<revision>/<release>/`, exatamente como o comentário dele pede.
  s3_prefix_v1 = "${local.models_prefix_base}/${local.releases.v1.revision}/v1/"
  s3_prefix_v2 = "${local.models_prefix_base}/${local.releases.v2.revision}/v2/"

  # terraform_data.artifacts_bucket (s3.tf, dono A6a) é referenciado em vez do
  # local.bucket_artifacts_name puro para criar uma dependência explícita de
  # ordem no grafo: o SageMaker só tenta ler o bucket depois do local-exec que
  # garante a existência dele (D2 — Academy nega aws_s3_bucket nativo).
  model_data_uri_v1 = "s3://${terraform_data.artifacts_bucket.output}/${local.s3_prefix_v1}"
  model_data_uri_v2 = "s3://${terraform_data.artifacts_bucket.output}/${local.s3_prefix_v2}"

  # Nomes de model/endpoint-config são domínio exclusivo do A6b (o nome do
  # endpoint em si vem de local.endpoint_v1_name/endpoint_v2_name, já
  # definidos em locals.tf do A6a pela fórmula de config/lab.yaml).
  model_name_v1 = "${local.project_prefix}-slm-v1-model-${local.suffix}"
  model_name_v2 = "${local.project_prefix}-slm-v2-model-${local.suffix}"

  endpoint_config_name_v1 = "${local.project_prefix}-slm-v1-cfg-${local.suffix}"
  endpoint_config_name_v2 = "${local.project_prefix}-slm-v2-cfg-${local.suffix}"
}

# ---------------------------------------------------------------------------
# V1 — release manual (Q4_0), capacidade fixa 1, sem autoscaling.
# ---------------------------------------------------------------------------

resource "aws_sagemaker_model" "v1" {
  count = var.enable_v1 ? 1 : 0

  name               = local.model_name_v1
  execution_role_arn = local.execution_role_arn
  # CKV_AWS_370 — o container do llama.cpp só serve inferência (recebe requests
  # da própria infra do SageMaker e lê o modelo que o SageMaker já copiou para
  # /opt/ml/model antes do container subir); nunca precisa iniciar conexão de
  # saída, então isolar a rede do container não quebra nada e reduz superfície.
  enable_network_isolation = true

  primary_container {
    image = local.llamacpp_image_uri

    model_data_source {
      s3_data_source {
        s3_uri           = local.model_data_uri_v1
        s3_data_type     = "S3Prefix"
        compression_type = "None"
      }
    }

    environment = {
      LLAMA_ARG_MODEL    = "/opt/ml/model/${local.releases.v1.filename}"
      LLAMA_ARG_CTX_SIZE = local.llamacpp_ctx_size
    }
  }

  lifecycle {
    precondition {
      condition     = local.bucket_artifacts_name != ""
      error_message = "bucket_artifacts_name vazio — a fundação do A6a (s3.tf) precisa existir antes deste apply."
    }
  }
}

resource "aws_sagemaker_endpoint_configuration" "v1" {
  count = var.enable_v1 ? 1 : 0
  #checkov:skip=CKV_AWS_98:sem CMK custom no Academy (SCP nega gestão de KMS além do
  # default da conta) e o alias/aws/sagemaker gerenciado ainda não existe nesta conta
  # (confirmado por `aws kms describe-key` real: NotFoundException) — depender da sua
  # criação lazy pelo próprio SageMaker no meio do apply é risco desnecessário para um
  # endpoint de laboratório sem dado sensível no volume ML.

  name = local.endpoint_config_name_v1

  production_variants {
    variant_name           = local.releases.v1.variant_name
    model_name             = aws_sagemaker_model.v1[0].name
    initial_instance_count = 1
    instance_type          = local.sagemaker_instance_type
    initial_variant_weight = 1
  }
}

# Endpoint em tempo real — o único recurso deste par de arquivos cobrado por hora.
# O provider espera InService: um apply verde aqui já é a prova real exigida pela
# missão, sem precisar de describe-endpoint manual (mas ele também é coletado
# para o relatório).
resource "aws_sagemaker_endpoint" "v1" {
  count = var.enable_v1 ? 1 : 0

  name                 = local.endpoint_v1_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.v1[0].name
}

# ---------------------------------------------------------------------------
# V2 — release pelo pipeline (Q4_K_M), autoscaling em autoscaling.tf.
# Bloco de recursos totalmente independente do de V1: ligar enable_v2 nunca
# gera diff em aws_sagemaker_model.v1/aws_sagemaker_endpoint_configuration.v1/
# aws_sagemaker_endpoint.v1 — essa é a prova de faseamento pedida pela missão.
# ---------------------------------------------------------------------------

resource "aws_sagemaker_model" "v2" {
  count = var.enable_v2 ? 1 : 0

  name               = local.model_name_v2
  execution_role_arn = local.execution_role_arn
  # Mesmo raciocínio do modelo v1 (CKV_AWS_370): container só serve inferência,
  # nunca inicia conexão de saída.
  enable_network_isolation = true

  primary_container {
    image = local.llamacpp_image_uri

    model_data_source {
      s3_data_source {
        s3_uri           = local.model_data_uri_v2
        s3_data_type     = "S3Prefix"
        compression_type = "None"
      }
    }

    environment = {
      LLAMA_ARG_MODEL    = "/opt/ml/model/${local.releases.v2.filename}"
      LLAMA_ARG_CTX_SIZE = local.llamacpp_ctx_size
    }
  }

  lifecycle {
    precondition {
      condition     = local.bucket_artifacts_name != ""
      error_message = "bucket_artifacts_name vazio — a fundação do A6a (s3.tf) precisa existir antes deste apply."
    }
  }
}

resource "aws_sagemaker_endpoint_configuration" "v2" {
  count = var.enable_v2 ? 1 : 0
  #checkov:skip=CKV_AWS_98:mesma justificativa do v1 — sem CMK custom no Academy e sem
  # o alias/aws/sagemaker gerenciado criado nesta conta.

  name = local.endpoint_config_name_v2

  production_variants {
    variant_name           = local.releases.v2.variant_name
    model_name             = aws_sagemaker_model.v2[0].name
    initial_instance_count = 1
    instance_type          = local.sagemaker_instance_type
    initial_variant_weight = 1
  }
}

resource "aws_sagemaker_endpoint" "v2" {
  count = var.enable_v2 ? 1 : 0

  name                 = local.endpoint_v2_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.v2[0].name
}
