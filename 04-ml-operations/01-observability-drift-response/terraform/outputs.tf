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
  description = "Bucket do lab com os dados de entrada, os metadados, a saída do treino e os incidentes."
  value       = terraform_data.bucket.output
}

output "execution_role_arn" {
  description = "Role já provisionada que o SageMaker e a Lambda assumem."
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

output "training_image" {
  description = "Imagem gerenciada usada no treino e na inferência."
  value       = var.training_image
}

output "instance_type" {
  description = "Tipo de instância usado no treino e no serving."
  value       = var.instance_type
}

output "model_artifact_uri" {
  description = "URI do artefato resolvida pelo DescribeTrainingJob (vazia antes do estágio de serving)."
  value       = var.model_artifact_uri
}

output "model_lineage" {
  description = "Versão didática do modelo em operação. O lab não treina churn-v2."
  value       = local.tags.model_lineage
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
  description = "Nome do endpoint que baseline/drift invocam, ou vazio antes do deploy."
  value       = var.deploy_serving ? aws_sagemaker_endpoint.churn[0].name : ""
}

# --------------------------------------------------------------------------- #
# Observabilidade — o que o `make dashboard` e o `make alarm-status` consomem
# --------------------------------------------------------------------------- #

output "metrics_namespace" {
  description = "Namespace em que as métricas de ML são publicadas."
  value       = var.metrics_namespace
}

output "dashboard_name" {
  description = "Nome do dashboard do CloudWatch criado para esta execução."
  value       = var.deploy_serving ? aws_cloudwatch_dashboard.mlops[0].dashboard_name : ""
}

output "dashboard_url" {
  description = "Link direto para o dashboard no console. O aluno não deve caçar o nome à mão."
  value       = var.deploy_serving ? local.dashboard_url : ""
}

output "alarm_name" {
  description = "Nome do alarme de drift, consultado pelo `make alarm-status`."
  value       = var.deploy_serving ? aws_cloudwatch_metric_alarm.data_drift[0].alarm_name : ""
}

output "alarm_url" {
  description = "Link direto para o alarme no console."
  value       = var.deploy_serving ? local.alarm_url : ""
}

output "alarm_threshold" {
  description = "Limiar de DataDriftPSIMax que leva o alarme a ALARM."
  value       = var.drift_alarm_threshold
}

output "event_rule_name" {
  description = "Regra do EventBridge que liga o alarme à Lambda."
  value       = var.deploy_serving ? aws_cloudwatch_event_rule.drift_alarm[0].name : ""
}

output "lambda_function_name" {
  description = "Lambda de reação, cujo log o `make reaction` inspeciona."
  value       = var.deploy_serving ? aws_lambda_function.drift_response[0].function_name : ""
}

output "lambda_log_group" {
  description = "Log group da Lambda, removido no destroy junto com a função."
  value       = var.deploy_serving ? aws_cloudwatch_log_group.drift_response[0].name : ""
}

output "incidents_prefix" {
  description = "Prefixo no S3 em que a Lambda escreve o JSON de incidente."
  value       = local.s3_prefixes.incidents
}

output "deploy_serving" {
  description = "Qual estágio o state representa neste momento."
  value       = var.deploy_serving
}
