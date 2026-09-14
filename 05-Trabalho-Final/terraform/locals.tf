# Um sufixo aleatório por ciclo de vida, guardado no state. O nome do bucket
# continua determinístico (o ID da conta já é globalmente único), mas o
# SageMaker recusa reutilizar um nome de training job/model/endpoint que já
# exista no histórico da conta — incluindo um `make finish` anterior. Sem um
# sufixo por ciclo de vida, a segunda execução do trabalho final falharia com
# ResourceInUse.
resource "random_id" "lifecycle" {
  byte_length = 4
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  suffix     = random_id.lifecycle.hex

  # Nome curto que aparece em todo recurso do trabalho final. Congelado pelo
  # contrato: local.prefix = "fiap-final-${var.student_id}".
  prefix = "fiap-final-${var.student_id}"

  bucket_name = "${local.prefix}-${local.account_id}"

  training_job_name = "${local.prefix}-train-${local.suffix}"
  model_name        = "${local.prefix}-model-${local.suffix}"

  realtime_endpoint_config_name   = "${local.prefix}-rt-epc-${local.suffix}"
  realtime_endpoint_name          = "${local.prefix}-rt-${local.suffix}"
  serverless_endpoint_config_name = "${local.prefix}-sl-epc-${local.suffix}"
  serverless_endpoint_name        = "${local.prefix}-sl-${local.suffix}"
  async_endpoint_config_name      = "${local.prefix}-async-epc-${local.suffix}"
  async_endpoint_name             = "${local.prefix}-async-${local.suffix}"

  realtime_scaling_policy_name = "${local.prefix}-rt-target"
  async_scaling_policy_name    = "${local.prefix}-async-target"

  # Alarmes com nome próprio para PSI de dados e PSI de predição: o Lab 04.1
  # já mostrou que os dois merecem métrica e alarme separados, e o dashboard
  # do trabalho final (A6) precisa apontar para cada um.
  data_drift_alarm_name       = "${local.prefix}-data-drift-${local.suffix}"
  prediction_drift_alarm_name = "${local.prefix}-prediction-drift-${local.suffix}"
  event_rule_name             = "${local.prefix}-drift-rule-${local.suffix}"
  lambda_name                 = "${local.prefix}-drift-response-${local.suffix}"
  lambda_package_path         = "${path.module}/../build/drift_response.zip"

  # Sem sufixo: PutDashboard é um upsert idempotente, então não há
  # ResourceInUse a evitar, e um nome estável é o que permite ao aluno abrir
  # sempre o mesmo link entre uma execução e a próxima.
  dashboard_name = "${local.prefix}-dashboard"
  dashboard_url = join("", [
    "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home",
    "?region=${var.aws_region}",
    "#dashboards/dashboard/${local.dashboard_name}",
  ])

  # Prefixos do bucket único do trabalho final. Cada agente de dados/serving/
  # observabilidade lê o prefixo que precisa daqui — nenhum módulo monta um
  # caminho de S3 por conta própria.
  s3_prefixes = {
    data         = "data"
    artifacts    = "artifacts"
    async_input  = "async/input"
    async_output = "async/output"
    incidents    = "incidents"
    metadata     = "metadata"
  }

  async_output_uri = "s3://${local.bucket_name}/${local.s3_prefixes.async_output}/"

  # Nenhum dado pessoal nas tags: elas caem em relatórios de custo que a turma
  # toda vê. `capability` e `model_lineage` registram que o trabalho final
  # opera a MESMA capacidade de churn dos labs anteriores, não um caso novo.
  tags = {
    course        = "cloud-based-machine-learning"
    project       = "05-final-project"
    capability    = "bora-fibra-churn"
    model_lineage = "churn-v1"
    student_id    = var.student_id
    purpose       = "education"
    managed_by    = "terraform"
  }

  # O mesmo conjunto de tags no formato que a API do S3 espera, para o bucket
  # que a CLI cria (veja s3.tf) — o default_tags do provider só alcança
  # recursos que o próprio provider cria.
  bucket_tagging_json = jsonencode({
    TagSet = [for k, v in local.tags : { Key = k, Value = v }]
  })
}
