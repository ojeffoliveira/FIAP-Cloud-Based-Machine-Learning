# Training job do SageMaker — a linhagem churn-v1.
#
# Dois comportamentos deste recurso no provider 6.60.0 moldam todo o desenho:
#   1. o create retorna assim que o job entra em InProgress — ele NÃO espera pelo
#      Completed, então um apply verde não é um modelo treinado;
#   2. ele exporta apenas `arn` e `tags_all` — não existe URI de artefato
#      calculada.
# Por isso o artefato é resolvido por fora, pelo `scripts/lab.py wait-training`.
#
# O `s3_data_distribution_type` é declarado explicitamente em todo canal: quando
# omitido, o provider 6.60.0 envia "ShardedByS3Key" e depois derruba o apply com
# "Provider produced inconsistent result after apply". O padrão da API da AWS é
# "FullyReplicated", que é o que um job de uma instância treinando no dataset
# inteiro precisa.
resource "aws_sagemaker_training_job" "churn" {
  training_job_name = local.training_job_name
  role_arn          = data.aws_iam_role.lab_role.arn

  hyper_parameters = var.hyperparameters

  # Deixado desligado para casar com a verificação prévia do Academy que se sabe
  # que funciona.
  enable_network_isolation = false

  algorithm_specification {
    training_image      = var.training_image
    training_input_mode = "File"
  }

  input_data_config {
    channel_name = "train"
    content_type = "text/csv"
    input_mode   = "File"

    data_source {
      s3_data_source {
        s3_data_type              = "S3Prefix"
        s3_uri                    = local.train_channel_uri
        s3_data_distribution_type = "FullyReplicated"
      }
    }
  }

  input_data_config {
    channel_name = "validation"
    content_type = "text/csv"
    input_mode   = "File"

    data_source {
      s3_data_source {
        s3_data_type              = "S3Prefix"
        s3_uri                    = local.validation_channel_uri
        s3_data_distribution_type = "FullyReplicated"
      }
    }
  }

  output_data_config {
    s3_output_path = local.training_output_uri
  }

  resource_config {
    instance_count    = 1
    instance_type     = var.instance_type
    volume_size_in_gb = var.volume_size_in_gb
  }

  stopping_condition {
    max_runtime_in_seconds = var.max_runtime_in_seconds
  }

  depends_on = [
    aws_s3_object.train,
    aws_s3_object.validation,
  ]
}
