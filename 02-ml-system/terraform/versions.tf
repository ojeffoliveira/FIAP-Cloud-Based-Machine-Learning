terraform {
  # Fixado exatamente, não com "~>": uma sala em que dois alunos resolvem versões
  # diferentes é uma sala depurando Terraform em vez de sistemas de ML.
  required_version = "= 1.15.8"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.60.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "= 3.9.0"
    }
  }
}
