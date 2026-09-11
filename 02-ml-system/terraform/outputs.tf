# Only non-secret identifiers. Temporary Academy credentials are never exposed
# here, in state, or in logs.

output "region" {
  description = "Region every resource lives in."
  value       = var.region
}

output "account_id" {
  description = "AWS account ID that owns the lab resources."
  value       = data.aws_caller_identity.current.account_id
}

output "bucket_name" {
  description = "Lab bucket holding input data, metadata and training output."
  value       = terraform_data.bucket.output
}

output "execution_role_arn" {
  description = "Pre-provisioned role SageMaker assumes."
  value       = data.aws_iam_role.lab_role.arn
}

output "training_job_name" {
  description = "Name to pass to DescribeTrainingJob."
  value       = aws_sagemaker_training_job.churn.training_job_name
}

output "training_output_uri" {
  description = "S3 prefix under which SageMaker writes model.tar.gz."
  value       = local.training_output_uri
}

output "training_channels" {
  description = "S3 URIs the training job reads."
  value = {
    train      = local.train_channel_uri
    validation = local.validation_channel_uri
  }
}

output "training_image" {
  description = "Managed image used for training and for inference."
  value       = var.training_image
}

output "hyperparameters" {
  description = "Hyperparameters submitted to the training job."
  value       = var.hyperparameters
}

output "instance_type" {
  description = "Instance type used for training and serving."
  value       = var.instance_type
}

output "model_artifact_uri" {
  description = "Artifact URI resolved from DescribeTrainingJob (empty before the serving stage)."
  value       = var.model_artifact_uri
}

output "model_name" {
  description = "SageMaker Model name, or empty while only training exists."
  value       = var.deploy_serving ? aws_sagemaker_model.churn[0].name : ""
}

output "endpoint_config_name" {
  description = "EndpointConfig name, or empty while only training exists."
  value       = var.deploy_serving ? aws_sagemaker_endpoint_configuration.churn[0].name : ""
}

output "endpoint_name" {
  description = "Endpoint name used by predict/evaluate, or empty before deployment."
  value       = var.deploy_serving ? aws_sagemaker_endpoint.churn[0].name : ""
}

output "deploy_serving" {
  description = "Which stage the state currently represents."
  value       = var.deploy_serving
}
