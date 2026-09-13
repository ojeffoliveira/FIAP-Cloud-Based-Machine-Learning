terraform {
  # Fixado exatamente, na mesma major do Lab 04.1 (04-ml-operations/01-*):
  # uma sala inteira resolvendo a mesma versão de provider é o que evita
  # depurar Terraform em vez de SageMaker. required_version acompanha o que
  # o .devcontainer da disciplina instala.
  required_version = "= 1.15.8"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.60.0"
    }
    # Só data source (nenhum recurso): resolve o owner do GitHub sem duplicar
    # em HCL o parsing que src/lab42/aws.py:github_owner() já implementa em
    # Python (ver locals.tf). Único jeito portátil de ler
    # GITHUB_REPOSITORY_OWNER/remote.origin.url de dentro do Terraform.
    external = {
      source  = "hashicorp/external"
      version = "= 2.3.5"
    }
  }
}
