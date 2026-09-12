# O único Model para o qual todo modo de serving abaixo aponta. "Um modelo,
# quatro formas de consumir" só se sustenta se as três EndpointConfigs
# referenciarem este mesmo recurso - nunca uma cópia do artefato por modo.
resource "aws_sagemaker_model" "churn" {
  count = var.deploy_serving ? 1 : 0

  name               = local.model_name
  execution_role_arn = data.aws_iam_role.lab_role.arn

  primary_container {
    image          = var.training_image
    model_data_url = var.model_artifact_uri
  }

  lifecycle {
    precondition {
      condition     = var.model_artifact_uri != ""
      error_message = "model_artifact_uri está vazio. Rode `make apply`, que resolve esse valor pelo DescribeTrainingJob antes de publicar."
    }
  }
}
