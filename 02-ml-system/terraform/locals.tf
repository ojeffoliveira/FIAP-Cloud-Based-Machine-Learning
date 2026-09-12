# Um sufixo aleatório por ciclo de vida, guardado no state.
#
# O nome do bucket continua determinístico (o ID da conta já é globalmente
# único), mas o SageMaker recusa reutilizar um nome de training job que exista no
# histórico - incluindo um job concluído de um `make e2e` anterior. Sem um sufixo
# por ciclo de vida, a segunda execução do lab falharia com ResourceInUse, então
# o sufixo é o mínimo de não-determinismo necessário para o lab ser repetível sem
# edição manual. Ele é gerado uma vez, persistido no state, e só muda depois de
# um destroy.
resource "random_id" "lifecycle" {
  byte_length = 4
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  suffix     = random_id.lifecycle.hex

  bucket_name          = "${var.project_prefix}-${local.account_id}"
  training_job_name    = "${var.project_prefix}-train-${local.suffix}"
  model_name           = "${var.project_prefix}-model-${local.suffix}"
  endpoint_config_name = "${var.project_prefix}-epc-${local.suffix}"
  endpoint_name        = "${var.project_prefix}-ep-${local.suffix}"

  data_dir = "${path.module}/${var.data_dir}"

  s3_prefixes = {
    train      = "input/train"
    validation = "input/validation"
    output     = "output/training"
    metadata   = "metadata"
  }

  train_channel_uri      = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.train}/"
  validation_channel_uri = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.validation}/"
  training_output_uri    = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.output}/"

  # Nenhum dado pessoal nas tags: elas caem em relatórios de custo que a turma toda vê.
  tags = {
    course     = "cloud-based-machine-learning"
    lab        = "lab1-model-to-ml-system"
    purpose    = "education"
    managed_by = "terraform"
  }

  # O mesmo conjunto de tags no formato que a API do S3 espera, para o bucket que
  # a CLI cria (veja s3.tf) - o default_tags do provider só alcança recursos que o
  # próprio provider cria.
  bucket_tagging_json = jsonencode({
    TagSet = [for k, v in local.tags : { Key = k, Value = v }]
  })
}
