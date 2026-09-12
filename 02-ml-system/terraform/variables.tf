variable "region" {
  description = "Região da AWS. O Academy Learner Lab só permite us-east-1."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.region == "us-east-1"
    error_message = "Este lab é validado só em us-east-1, a região que o AWS Academy permite."
  }
}

variable "project_prefix" {
  description = "Prefixo semântico curto para todo nome de recurso. Espelhado em config/lab.yaml."
  type        = string
  default     = "prb-cloud-ml-lab1"

  validation {
    condition     = can(regex("^[a-z0-9-]{3,32}$", var.project_prefix))
    error_message = "O prefixo precisa ser letras minúsculas, dígitos e hifens (regra de nome do S3 e do SageMaker)."
  }
}

variable "execution_role_name" {
  description = "Role já provisionada pelo Academy. O lab não tem permissão para criar role de IAM."
  type        = string
  default     = "LabRole"
}

variable "data_dir" {
  description = "Diretório com o dataset gerado que sobe para o S3."
  type        = string
  default     = "../artifacts/data"
}

variable "training_image" {
  description = "Imagem gerenciada do XGBoost nativo do SageMaker. Treino e serving compartilham a mesma."
  type        = string
  default     = "683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-xgboost:1.7-1"
}

variable "instance_type" {
  description = "Tipo de instância para o treino e para a variant do endpoint."
  type        = string
  default     = "ml.m5.large"
}

variable "volume_size_in_gb" {
  description = "Volume EBS do treino. 30 GB é o tamanho validado na verificação prévia do Academy."
  type        = number
  default     = 30
}

variable "max_runtime_in_seconds" {
  description = "Parada dura do training job, para uma execução travada não drenar o crédito do lab."
  type        = number
  default     = 900

  validation {
    condition     = var.max_runtime_in_seconds >= 600 && var.max_runtime_in_seconds <= 900
    error_message = "Mantenha a condição de parada entre 600 e 900 segundos (spec 9.4)."
  }
}

variable "hyperparameters" {
  description = "Hiperparâmetros do XGBoost nativo. Os valores são strings, como a API exige."
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
# Passagem de bastão entre os dois estágios
# --------------------------------------------------------------------------- #

variable "deploy_serving" {
  description = <<-EOT
    Portão de estágio. Com false, cria só storage + treino; com true, cria também
    o Model, o EndpointConfig e o Endpoint. O `make apply` vira essa chave
    automaticamente depois de localizar o artefato de treino e provar que existe.
  EOT
  type        = bool
  default     = false
}

variable "model_artifact_uri" {
  description = <<-EOT
    URI autoritativa do model.tar.gz no S3, obtida do DescribeTrainingJob pelo
    scripts/wait_training.py e escrita em artifact.auto.tfvars.json. Nunca um
    caminho montado à mão.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.model_artifact_uri == "" || can(regex("^s3://[a-z0-9.-]+/.+\\.tar\\.gz$", var.model_artifact_uri))
    error_message = "model_artifact_uri precisa estar vazio ou ser uma URI s3:// terminando em .tar.gz."
  }
}
