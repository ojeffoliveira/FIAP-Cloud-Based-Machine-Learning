# Somente identificadores não secretos. As credenciais temporárias do Academy
# nunca são expostas aqui, no state, nem em log.

output "region" {
  value = var.region
}

output "account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "bucket_name" {
  value = terraform_data.bucket.output
}

output "execution_role_arn" {
  value = data.aws_iam_role.lab_role.arn
}

output "training_job_name" {
  value = aws_sagemaker_training_job.churn.training_job_name
}

output "training_output_uri" {
  value = local.training_output_uri
}

output "training_channels" {
  value = {
    train      = local.train_channel_uri
    validation = local.validation_channel_uri
  }
}

output "training_image" {
  value = var.training_image
}

output "hyperparameters" {
  value = var.hyperparameters
}

output "instance_type" {
  value = var.instance_type
}

output "model_artifact_uri" {
  value = var.model_artifact_uri
}

output "model_name" {
  value = var.deploy_serving ? aws_sagemaker_model.churn[0].name : ""
}

output "realtime_endpoint_config_name" {
  value = var.deploy_serving ? aws_sagemaker_endpoint_configuration.realtime[0].name : ""
}

output "realtime_endpoint_name" {
  value = var.deploy_serving ? aws_sagemaker_endpoint.realtime[0].name : ""
}

output "serverless_endpoint_config_name" {
  value = var.deploy_serving ? aws_sagemaker_endpoint_configuration.serverless[0].name : ""
}

output "serverless_endpoint_name" {
  value = var.deploy_serving ? aws_sagemaker_endpoint.serverless[0].name : ""
}

output "async_endpoint_config_name" {
  value = var.deploy_serving ? aws_sagemaker_endpoint_configuration.async[0].name : ""
}

output "async_endpoint_name" {
  value = var.deploy_serving ? aws_sagemaker_endpoint.async[0].name : ""
}

output "async_output_uri" {
  value = local.async_output_uri
}

output "realtime_scalable_resource_id" {
  value = var.deploy_serving ? aws_appautoscaling_target.realtime[0].resource_id : ""
}

output "async_scalable_resource_id" {
  value = var.deploy_serving ? aws_appautoscaling_target.async[0].resource_id : ""
}

output "deploy_serving" {
  value = var.deploy_serving
}
