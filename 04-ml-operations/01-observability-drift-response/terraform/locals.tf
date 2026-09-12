# Um sufixo aleatório por ciclo de vida, guardado no state.
#
# O nome do bucket continua determinístico (o ID da conta já é globalmente único),
# mas o SageMaker recusa reutilizar um nome de training job que exista no
# histórico — incluindo um job concluído de um `make e2e` anterior. Sem um sufixo
# por ciclo de vida, a segunda execução do lab falharia com ResourceInUse. Ele é
# gerado uma vez, persistido no state, e só muda depois de um destroy.
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

  # O dashboard é o artefato que o aluno abre no console, então o nome é curto e
  # legível em vez de seguir o prefixo técnico dos recursos de compute.
  dashboard_name = "fiap-mlops-${local.suffix}"

  alarm_name          = "${var.project_prefix}-drift-${local.suffix}"
  event_rule_name     = "${var.project_prefix}-drift-rule-${local.suffix}"
  lambda_name         = "${var.project_prefix}-drift-response-${local.suffix}"
  lambda_package_path = "${path.module}/../build/drift_response.zip"

  data_dir = "${path.module}/${var.data_dir}"

  s3_prefixes = {
    train      = "input/train"
    validation = "input/validation"
    reference  = "input/reference"
    production = "input/production"
    output     = "output/training"
    metadata   = "metadata"
    incidents  = "incidents"
  }

  train_channel_uri      = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.train}/"
  validation_channel_uri = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.validation}/"
  training_output_uri    = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.output}/"

  # Link direto para o dashboard. Montado aqui e exposto como output para o
  # `make dashboard` imprimir: o aluno não deve precisar caçar o nome do dashboard
  # no console. O nome vai duas vezes na URL porque o console guarda o dashboard
  # selecionado no fragmento, depois do `#`.
  dashboard_url = join("", [
    "https://${var.region}.console.aws.amazon.com/cloudwatch/home",
    "?region=${var.region}",
    "#dashboards/dashboard/${local.dashboard_name}",
  ])

  alarm_url = join("", [
    "https://${var.region}.console.aws.amazon.com/cloudwatch/home",
    "?region=${var.region}",
    "#alarmsV2:alarm/${local.alarm_name}",
  ])

  # Nenhum dado pessoal nas tags: elas caem em relatórios de custo que a turma toda vê.
  # `capability` e `model_lineage` registram que este lab opera a MESMA capacidade
  # de churn dos labs anteriores, e não um caso novo.
  tags = {
    course        = "cloud-based-machine-learning"
    lab           = "lab04-ml-operations"
    capability    = "bora-fibra-churn"
    model_lineage = "churn-v1"
    purpose       = "education"
    managed_by    = "terraform"
  }

  # O mesmo conjunto de tags no formato que a API do S3 espera, para o bucket que a
  # CLI cria (veja s3.tf) — o default_tags do provider só alcança recursos que o
  # próprio provider cria.
  bucket_tagging_json = jsonencode({
    TagSet = [for k, v in local.tags : { Key = k, Value = v }]
  })
}
