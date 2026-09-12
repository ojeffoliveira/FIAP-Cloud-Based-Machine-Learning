# Estágio de serving. Criado só quando var.deploy_serving é true, ou seja, depois
# de o training job terminar e o artefato dele ser comprovado no S3.
#
# O container de inferência é a mesma imagem que treinou o modelo: divergência
# entre o runtime de treino e o de serving é uma das formas clássicas de um
# sistema de ML quebrar depois que "o modelo funcionou".

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

resource "aws_sagemaker_endpoint_configuration" "churn" {
  count = var.deploy_serving ? 1 : 0

  name = local.endpoint_config_name

  production_variants {
    variant_name           = "AllTraffic"
    model_name             = aws_sagemaker_model.churn[0].name
    initial_instance_count = 1
    instance_type          = var.instance_type
    initial_variant_weight = 1
  }
}
