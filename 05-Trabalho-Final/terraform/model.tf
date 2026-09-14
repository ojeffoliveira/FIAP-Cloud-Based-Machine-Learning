# Um unico Model reaproveitado pelos tres candidatos de serving abaixo
# (Real-Time, Serverless, Async): "um modelo, tres formas de consumir" so se
# sustenta se as tres EndpointConfiguration apontarem para este mesmo
# recurso -- nunca uma copia do artefato por modo. Mesmo desenho do lab 03
# (03-serving-and-scaling/terraform/model.tf), que ja rodou com tres
# endpoints simultaneos nesta conta do Academy (ver ACADEMY.md item 13).
resource "aws_sagemaker_model" "churn" {
  count = var.enable_serving ? 1 : 0

  name               = local.model_name
  execution_role_arn = data.aws_iam_role.lab.arn

  primary_container {
    image          = var.training_image
    model_data_url = var.model_artifact_s3_uri
  }

  # var.model_artifact_s3_uri nunca e construido por convencao (ver
  # CONTRATO.md) -- vem de DescribeTrainingJob + HeadObject, gravado em
  # .generated/artifact.auto.tfvars.json pela automacao do estagio 2. Um
  # apply com enable_serving=true e esse arquivo ausente/vazio precisa
  # falhar aqui, cedo, em vez de criar um Model apontando para nada.
  lifecycle {
    precondition {
      condition     = var.model_artifact_s3_uri != ""
      error_message = "model_artifact_s3_uri esta vazio. O estagio 2 (enable_serving=true) exige o artefato do treino ja resolvido via DescribeTrainingJob + HeadObject, gravado em .generated/artifact.auto.tfvars.json."
    }
  }
}
