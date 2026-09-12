# Um sufixo aleatório por ciclo de vida, guardado no state. O SageMaker recusa
# reutilizar um nome de job/model/endpoint que exista no histórico, então um
# sufixo novo por ciclo de vida é o mínimo de não-determinismo necessário para o
# `make apply` ser repetível sem edição manual. O nome do bucket continua
# determinístico (o ID da conta já é globalmente único), para o `verify-clean`
# achá-lo só pelo prefixo.
resource "random_id" "lifecycle" {
  byte_length = 4
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  suffix     = random_id.lifecycle.hex

  bucket_name = "${var.project_prefix}-${local.account_id}-${var.region}"

  training_job_name = "${var.project_prefix}-train-${local.suffix}"
  model_name        = "${var.project_prefix}-model-${local.suffix}"

  realtime_endpoint_config_name   = "${var.project_prefix}-rt-epc-${local.suffix}"
  realtime_endpoint_name          = "${var.project_prefix}-rt-${local.suffix}"
  serverless_endpoint_config_name = "${var.project_prefix}-sl-epc-${local.suffix}"
  serverless_endpoint_name        = "${var.project_prefix}-sl-${local.suffix}"
  async_endpoint_config_name      = "${var.project_prefix}-async-epc-${local.suffix}"
  async_endpoint_name             = "${var.project_prefix}-async-${local.suffix}"

  realtime_scaling_policy_name = "${var.project_prefix}-rt-target"
  async_scaling_policy_name    = "${var.project_prefix}-async-target"

  data_dir = "${path.module}/${var.data_dir}"

  s3_prefixes = {
    train        = "input/train"
    validation   = "input/validation"
    output       = "output/training"
    metadata     = "metadata"
    async_output = "async/output"
  }

  train_channel_uri      = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.train}/"
  validation_channel_uri = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.validation}/"
  training_output_uri    = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.output}/"
  async_output_uri       = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.async_output}/"

  realtime_variant_resource_id = "endpoint/${local.realtime_endpoint_name}/variant/AllTraffic"
  async_variant_resource_id    = "endpoint/${local.async_endpoint_name}/variant/AllTraffic"

  # Conjunto exato de tags exigido pela spec - separado das tags livres da
  # disciplina usadas em outros pontos, mantido literal para relatórios de custo e
  # de inventário conseguirem filtrar por ele.
  tags = {
    Project   = "FIAP-Cloud-Based-Machine-Learning"
    Lab       = "03-serving-and-scaling"
    ManagedBy = "Terraform"
    Owner     = "student"
  }

  # O mesmo conjunto de tags no formato que a API do S3 espera, para o bucket que
  # a CLI cria (veja s3.tf) - o default_tags do provider só alcança recursos que o
  # próprio provider cria.
  bucket_tagging_json = jsonencode({
    TagSet = [for k, v in local.tags : { Key = k, Value = v }]
  })
}
