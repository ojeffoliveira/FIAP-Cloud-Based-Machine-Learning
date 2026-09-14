# --------------------------------------------------------------------------- #
# Congeladas pelo contrato do trabalho final (CONTRATO.md) — nenhuma delas é
# preenchida pelo aluno; todas vêm com default ou são escritas pela automação.
# --------------------------------------------------------------------------- #

variable "aws_region" {
  description = "Região da AWS. O AWS Academy Learner Lab só permite us-east-1."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "O trabalho final é validado só em us-east-1, a região que o AWS Academy permite."
  }
}

variable "student_id" {
  description = <<-EOT
    Identificador curto do aluno/grupo, usado em todo nome de recurso via
    local.prefix. Gerado pela automação a partir de TF_VAR_student_id
    (scripts/setup.sh) — o aluno nunca informa este valor.
  EOT
  type        = string
  default     = "aluno"

  validation {
    condition     = can(regex("^[a-z0-9-]{1,20}$", var.student_id))
    error_message = "student_id precisa ser letras minúsculas, dígitos e hifens (regra de nome do S3 e do SageMaker)."
  }
}

variable "enable_serving" {
  description = <<-EOT
    Portão entre os dois estágios, congelado no contrato do trabalho final.
    Com false, o apply cria só storage; com true, cria também Model, os três
    endpoints (Real-Time/Serverless/Async), autoscaling, dashboard, alarmes,
    EventBridge e Lambda. `make deploy` vira essa chave automaticamente depois
    de localizar o artefato de treino e provar que existe via API.
  EOT
  type        = bool
  default     = false
}

variable "model_artifact_s3_uri" {
  description = <<-EOT
    URI autoritativa do model.tar.gz no S3, obtida de
    DescribeTrainingJob.ModelArtifacts.S3ModelArtifacts e confirmada por
    HeadObject. Escrita em .generated/artifact.auto.tfvars.json pela
    automação — nunca um caminho montado por convenção.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.model_artifact_s3_uri == "" || can(regex("^s3://[a-z0-9.-]+/.+\\.tar\\.gz$", var.model_artifact_s3_uri))
    error_message = "model_artifact_s3_uri precisa estar vazio ou ser uma URI s3:// terminando em .tar.gz."
  }
}

variable "psi_threshold" {
  description = <<-EOT
    Limiar didático de DataDriftPSIMax/PredictionDriftPSI que abre incidente.
    O mesmo valor que src/final_project/config.py expõe para o lado Python:
    um único número, para os dois lados nunca saírem de sincronia.
  EOT
  type        = number
  default     = 0.20

  validation {
    condition     = var.psi_threshold > 0 && var.psi_threshold < 1
    error_message = "psi_threshold precisa estar entre 0 e 1."
  }
}

# --------------------------------------------------------------------------- #
# Execução, imagem e treino — consumidas por A4 (terraform/training.tf,
# boto3) e A5 (model.tf, realtime.tf, serverless.tf, async.tf, autoscaling.tf).
# --------------------------------------------------------------------------- #

variable "execution_role_name" {
  description = "Role já provisionada pelo Academy. O trabalho final não tem permissão para criar role de IAM."
  type        = string
  default     = "LabRole"
}

variable "training_image" {
  description = "Imagem gerenciada do XGBoost nativo do SageMaker. Treino e os quatro modos de serving compartilham a mesma."
  type        = string
  default     = "683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-xgboost:1.7-1"
}

variable "instance_type" {
  description = "Tipo de instância padrão do Academy, usado no treino, no Real-Time e no Async. Serverless e Batch Transform têm variáveis próprias de capacidade."
  type        = string
  default     = "ml.m5.large"
}

variable "volume_size_in_gb" {
  description = "Volume EBS do training job."
  type        = number
  default     = 30
}

variable "max_runtime_in_seconds" {
  description = "Parada dura do training job, para uma execução travada não drenar o crédito do Academy."
  type        = number
  default     = 900

  validation {
    condition     = var.max_runtime_in_seconds >= 600 && var.max_runtime_in_seconds <= 900
    error_message = "Mantenha a condição de parada entre 600 e 900 segundos."
  }
}

variable "hyperparameters" {
  description = "Hiperparâmetros do XGBoost nativo, alinhados à linhagem Bora Fibra dos labs anteriores."
  type        = map(string)
  default = {
    objective        = "binary:logistic"
    eval_metric      = "auc"
    num_round        = "50"
    max_depth        = "4"
    eta              = "0.10"
    subsample        = "0.90"
    colsample_bytree = "0.90"
    verbosity        = "1"
  }
}

# --------------------------------------------------------------------------- #
# Serverless Inference (A5)
# --------------------------------------------------------------------------- #

variable "serverless_memory_size_in_mb" {
  description = "Memória da variant serverless. 2048 MB é o menor tamanho confortável para este XGBoost nativo."
  type        = number
  default     = 2048
}

variable "serverless_max_concurrency" {
  description = "Concorrência máxima da variant serverless."
  type        = number
  default     = 5
}

# --------------------------------------------------------------------------- #
# Asynchronous Inference + Application Auto Scaling 0-1 (A5)
# --------------------------------------------------------------------------- #

variable "async_max_concurrent_invocations_per_instance" {
  type    = number
  default = 1
}

variable "async_min_capacity" {
  description = "Mínimo de instâncias da variant async. 0 é o que torna o padrão elegível quando ninguém está usando."
  type        = number
  default     = 0
}

variable "async_max_capacity" {
  type    = number
  default = 1
}

variable "async_target_backlog_per_instance" {
  description = "Alvo da política de target tracking em ApproximateBacklogSizePerInstance."
  type        = number
  default     = 5
}

# --------------------------------------------------------------------------- #
# Real-Time Endpoint + Application Auto Scaling (A5)
# --------------------------------------------------------------------------- #

variable "realtime_min_capacity" {
  type    = number
  default = 1
}

variable "realtime_max_capacity" {
  type    = number
  default = 2
}

variable "realtime_target_invocations_per_instance" {
  description = "Alvo do target tracking em SageMakerVariantInvocationsPerInstance."
  type        = number
  default     = 60
}

variable "realtime_scale_out_cooldown" {
  type    = number
  default = 60
}

variable "realtime_scale_in_cooldown" {
  type    = number
  default = 180
}

# --------------------------------------------------------------------------- #
# Batch Transform (A5) — job efêmero, criado por boto3 no momento de
# comparação/execução, nunca por um resource deste módulo. Só o tipo de
# instância é fixado aqui, para nenhuma decisão de infraestrutura acontecer
# fora dos arquivos .tf.
# --------------------------------------------------------------------------- #

variable "batch_instance_type" {
  type    = string
  default = "ml.m5.large"
}

# --------------------------------------------------------------------------- #
# Observabilidade (A6)
# --------------------------------------------------------------------------- #

variable "metrics_namespace" {
  description = "Namespace das métricas customizadas de ML. Python publica, alarme e dashboard leem — precisa ser o mesmo namespace de config/scenario.yaml."
  type        = string
  default     = "FIAP/BoraFibra/Final"
}

variable "alarm_period_seconds" {
  description = "Período de avaliação dos alarmes de drift. 60s é o menor valor que o CloudWatch aceita para métrica customizada de resolução padrão."
  type        = number
  default     = 60
}

variable "lambda_runtime" {
  description = "Runtime gerenciado da Lambda de reação a drift."
  type        = string
  default     = "python3.12"
}

variable "lambda_timeout_seconds" {
  description = "Timeout da Lambda. Ela só valida o evento, grava o incidente e publica a métrica de reação — nunca retreina, nunca troca modelo."
  type        = number
  default     = 30
}

variable "log_retention_days" {
  description = "Retenção do log da Lambda. Declarado explicitamente para o destroy levar o log group embora."
  type        = number
  default     = 7
}
