terraform {
  # Fixado exatamente, não com "~>": uma sala inteira resolvendo a mesma
  # versão de provider é o que evita depurar Terraform em vez de sistemas de
  # ML. O valor acompanha o que os labs anteriores já validaram nesta conta —
  # mudar aqui exige mudar em todos.
  required_version = "= 1.15.8"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.60.0"
    }
    # Sufixo de ciclo de vida (locals.tf): o SageMaker recusa reutilizar nome
    # de training job/model/endpoint que já exista no histórico da conta,
    # incluindo um `make finish` anterior.
    random = {
      source  = "hashicorp/random"
      version = "= 3.9.0"
    }
    # Empacota lambda/drift_response.py (entregue por A6) num zip no apply.
    # Declarado aqui, e não em lambda.tf, porque só A0 escreve versions.tf —
    # A6 só usa `data.archive_file`, nunca precisa tocar neste arquivo.
    archive = {
      source  = "hashicorp/archive"
      version = "= 2.7.1"
    }
  }
}
