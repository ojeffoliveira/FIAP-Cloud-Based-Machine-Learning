# Training job do SageMaker - o bootstrap que produz o único artefato de modelo
# que todo modo de serving abaixo vai consumir.
#
# Os mesmos dois comportamentos validados no 02-ml-system, provider 6.60.0:
#   1. o create retorna assim que o job entra em InProgress - o artefato só
#      existe depois que scripts/lab.py wait-training consulta até Completed;
#   2. o s3_data_distribution_type precisa ser explícito em todo canal, senão o
#      provider envia "ShardedByS3Key" e o apply falha depois com "Provider
#      produced inconsistent result after apply".
resource "aws_sagemaker_training_job" "churn" {
  training_job_name = local.training_job_name
  role_arn          = data.aws_iam_role.lab_role.arn

  hyper_parameters = var.hyperparameters

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
