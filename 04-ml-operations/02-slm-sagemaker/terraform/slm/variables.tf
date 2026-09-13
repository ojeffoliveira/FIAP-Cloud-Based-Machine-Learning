# Variáveis do stack terraform/slm. Único arquivo do stack que declara
# `variable` (regra do agente A6a) — A6b (sagemaker/autoscaling/dashboard)
# consome estas e as expostas em locals.tf/outputs.tf; se precisar de uma
# variável nova, define como `locals` dentro do próprio arquivo dele, para
# não colidir neste.

variable "region" {
  description = "Região da AWS. O Academy Learner Lab só permite us-east-1."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.region == "us-east-1"
    error_message = "Este lab é validado só em us-east-1, a região que o AWS Academy permite."
  }
}

variable "execution_role_name" {
  description = "Role já provisionada pelo Academy. O lab não tem permissão para criar role de IAM."
  type        = string
  default     = "LabRole"
}

# --------------------------------------------------------------------------- #
# Portão de release — consumido por A6b (SageMaker/autoscaling/dashboard) e
# pelo Makefile (`plan-v1`/`deploy-v1` já chamam `-var release=v1`).
# --------------------------------------------------------------------------- #

variable "release" {
  description = <<-EOT
    Release do SLM sendo planejada/aplicada: "v1" (manual, Q4_0, fixed_capacity)
    ou "v2" (pipeline via runner self-hosted, Q4_K_M, autoscaling). Escolhe
    qual model/releases/v{release}.yaml o restante do stack (A6b) e os
    scripts (A7/A8) consultam. Nunca as duas releases no ar ao mesmo tempo
    dentro do mesmo `terraform apply` — troque a variável para alternar.
  EOT
  type        = string
  default     = "v1"

  validation {
    condition     = contains(["v1", "v2"], var.release)
    error_message = "release precisa ser \"v1\" ou \"v2\"."
  }
}
