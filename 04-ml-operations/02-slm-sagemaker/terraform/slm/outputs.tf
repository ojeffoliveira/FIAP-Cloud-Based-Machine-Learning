# Somente identificadores não secretos. Credencial temporária do Academy
# nunca é exposta aqui, no state, nem em log (mesma regra do Lab 04.1).
#
# Contrato de consumo: A6b (sagemaker/autoscaling/dashboard) lê `terraform
# output -json` deste mesmo state para pegar bucket/role/nomes — nunca
# recalcula suffix/account_id com a própria fórmula.

output "region" {
  description = "Região em que todos os recursos do lab vivem."
  value       = var.region
}

output "account_id" {
  description = "ID da conta AWS dona dos recursos do lab (mesma STS de src/lab42/aws.py:account_id())."
  value       = local.account_id
}

output "github_owner" {
  description = "Owner do GitHub usado no sufixo determinístico."
  value       = local.github_owner
}

output "suffix" {
  description = "Sufixo determinístico de 8 caracteres (sha256(account_id:github_owner)[:8]) — mesmo valor de src/lab42/aws.py:suffix()."
  value       = local.suffix
}

output "project_prefix" {
  description = "Prefixo semântico de todo nome de recurso do lab."
  value       = local.project_prefix
}

output "release" {
  description = "Release corrente (\"v1\" ou \"v2\") aplicada neste state."
  value       = var.release
}

output "execution_role_arn" {
  description = "ARN da LabRole que o SageMaker assume (execução, não criação de IAM)."
  value       = local.execution_role_arn
}

# --------------------------------------------------------------------------- #
# S3
# --------------------------------------------------------------------------- #

output "bucket_state_name" {
  description = "Bucket de state do Terraform (criado por scripts/bootstrap_backend.py, fora deste apply)."
  value       = local.bucket_state_name
}

output "bucket_artifacts_name" {
  description = "Bucket de artifacts do SLM — onde model_sync.py (A7) publica o GGUF e de onde ModelDataSource (A6b) lê."
  value       = terraform_data.artifacts_bucket.output
}

output "models_prefix_base" {
  description = "Raiz do layout D7 (models/<repo>) — A6b/A7 completam com <revision>/<release>/<filename>."
  value       = local.models_prefix_base
}

# --------------------------------------------------------------------------- #
# D20: endpoint_v1_name/v2_name/endpoint_name_current e dashboard_name NÃO são
# output daqui — locals_slm.tf/outputs_slm.tf (A6b) são a fonte, já que o
# local correspondente também saiu deste arquivo (evita dois outputs com o
# mesmo nome no mesmo módulo).
# --------------------------------------------------------------------------- #

output "tags" {
  description = "Tags padrão do lab, propagadas via default_tags no provider."
  value       = local.tags
}
