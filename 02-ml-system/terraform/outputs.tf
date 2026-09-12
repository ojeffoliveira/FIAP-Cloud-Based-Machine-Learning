# Somente identificadores não secretos. As credenciais temporárias do Academy
# nunca são expostas aqui, no state, nem em log.

output "region" {
  description = "Região em que todos os recursos vivem."
  value       = var.region
}

output "account_id" {
  description = "ID da conta AWS dona dos recursos do lab."
  value       = data.aws_caller_identity.current.account_id
}

output "bucket_name" {
  description = "Bucket do lab com os dados de entrada, os metadados e a saída do treino."
  value       = terraform_data.bucket.output
}

output "execution_role_arn" {
  description = "Role já provisionada que o SageMaker assume."
  value       = data.aws_iam_role.lab_role.arn
}

output "training_job_name" {
  description = "Nome a passar para o DescribeTrainingJob."
  value       = aws_sagemaker_training_job.churn.training_job_name
}

output "training_output_uri" {
  description = "Prefixo no S3 onde o SageMaker escreve o model.tar.gz."
  value       = local.training_output_uri
}

output "training_channels" {
  description = "URIs do S3 que o training job lê."
  value = {
    train      = local.train_channel_uri
    validation = local.validation_channel_uri
  }
}

output "training_image" {
  description = "Imagem gerenciada usada no treino e na inferência."
  value       = var.training_image
}

output "hyperparameters" {
  description = "Hiperparâmetros enviados ao training job."
  value       = var.hyperparameters
}

output "instance_type" {
  description = "Tipo de instância usado no treino e no serving."
  value       = var.instance_type
}

output "model_artifact_uri" {
  description = "URI do artefato resolvida pelo DescribeTrainingJob (vazia antes do estágio de serving)."
  value       = var.model_artifact_uri
}

output "model_name" {
  description = "Nome do Model do SageMaker, ou vazio enquanto só existe o treino."
  value       = var.deploy_serving ? aws_sagemaker_model.churn[0].name : ""
}

output "endpoint_config_name" {
  description = "Nome do EndpointConfig, ou vazio enquanto só existe o treino."
  value       = var.deploy_serving ? aws_sagemaker_endpoint_configuration.churn[0].name : ""
}

output "endpoint_name" {
  description = "Nome do endpoint usado por predict/evaluate, ou vazio antes do deploy."
  value       = var.deploy_serving ? aws_sagemaker_endpoint.churn[0].name : ""
}

output "deploy_serving" {
  description = "Qual estágio o state representa neste momento."
  value       = var.deploy_serving
}
