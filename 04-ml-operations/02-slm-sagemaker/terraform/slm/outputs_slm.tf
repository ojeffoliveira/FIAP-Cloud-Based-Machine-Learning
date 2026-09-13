# Outputs do domínio SageMaker/autoscaling/dashboard. Arquivo separado de
# outputs.tf (dono: A6a) de propósito — ownership disjunta por arquivo.
#
# try() em todo output que depende de count: enable_v1/enable_v2 podem estar
# desligados num dado apply (ex.: plan de prova de faseamento com
# enable_v2=false), e indexar [0] num recurso com count=0 quebra o plan sem
# try().

output "endpoint_name_v1" {
  description = "Nome do endpoint SageMaker V1 (release Q4_0, capacidade fixa)."
  value       = try(aws_sagemaker_endpoint.v1[0].name, null)
}

output "endpoint_arn_v1" {
  description = "ARN do endpoint SageMaker V1."
  value       = try(aws_sagemaker_endpoint.v1[0].arn, null)
}

output "endpoint_name_v2" {
  description = "Nome do endpoint SageMaker V2 (release Q4_K_M, com autoscaling)."
  value       = try(aws_sagemaker_endpoint.v2[0].name, null)
}

output "endpoint_arn_v2" {
  description = "ARN do endpoint SageMaker V2."
  value       = try(aws_sagemaker_endpoint.v2[0].arn, null)
}

output "model_name_v1" {
  description = "Nome do aws_sagemaker_model V1."
  value       = try(aws_sagemaker_model.v1[0].name, null)
}

output "model_name_v2" {
  description = "Nome do aws_sagemaker_model V2."
  value       = try(aws_sagemaker_model.v2[0].name, null)
}

output "endpoint_config_name_v1" {
  description = "Nome da endpoint configuration V1."
  value       = try(aws_sagemaker_endpoint_configuration.v1[0].name, null)
}

output "endpoint_config_name_v2" {
  description = "Nome da endpoint configuration V2."
  value       = try(aws_sagemaker_endpoint_configuration.v2[0].name, null)
}

output "autoscaling_scalable_target_resource_id" {
  description = "Resource id do scalable target de V2 (application-autoscaling), formato endpoint/<nome>/variant/<variante>."
  value       = try(aws_appautoscaling_target.v2[0].resource_id, null)
}

output "autoscaling_policy_name" {
  description = "Nome da policy de target tracking de V2."
  value       = try(aws_appautoscaling_policy.v2_invocations_per_instance[0].name, null)
}

output "autoscaling_target_invocations_per_instance" {
  description = "Alvo (invocações/instância/min) usado na policy de V2 — ver raciocínio em autoscaling.tf."
  value       = local.autoscaling_target_invocations
}

output "dashboard_name" {
  description = "Nome do dashboard CloudWatch do lab."
  value       = try(aws_cloudwatch_dashboard.slm[0].dashboard_name, null)
}

output "dashboard_url" {
  description = "URL direta do dashboard no console CloudWatch."
  value       = try("https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.slm[0].dashboard_name}", null)
}

output "sagemaker_instance_type" {
  description = "Tipo de instância usado por V1 e V2 (ml.m5.xlarge — único aprovado nos guardrails do Academy)."
  value       = local.sagemaker_instance_type
}
