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

  # COM o sufixo do ciclo de vida, como os endpoints. A primeira versão usava nome
  # fixo, com o argumento de que o `verify-clean` acha pelo prefixo — o que é
  # verdade, e continua verdade com sufixo, porque a busca é por prefixo.
  #
  # O nome fixo custou um diagnóstico difícil: com dois ciclos de vida vivos na
  # mesma conta (duas pessoas rodando o lab, ou um apply novo antes de destruir o
  # anterior), o segundo apply SOBRESCREVE o painel do primeiro. O painel continua
  # abrindo e mostrando zero, porque aponta para os endpoints do outro ciclo — e
  # nada na tela avisa isso. Com o sufixo, cada ciclo tem o seu painel.
  dashboard_name      = "${var.project_prefix}-serving-${local.suffix}"
  dashboard_live_name = "${var.project_prefix}-serving-ao-vivo-${local.suffix}"

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
