terraform {
  # Fixado exatamente, no mesmo padrão do Lab 04.1: uma sala em que dois alunos
  # resolvem versões diferentes é uma sala depurando Terraform em vez de MLOps.
  # O valor acompanha o que o .devcontainer instala — mudar lá exige mudar aqui.
  required_version = "= 1.15.8"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.60.0"
    }
  }

  # Backend padrão (local): este stack só existe para o Codespaces gerenciar o
  # runner (spec 04.2 §6.3/§13). O pipeline de deploy V2 nunca roda terraform
  # aqui — ele só usa a instância EC2 já criada. terraform/slm/ (agente A6) é
  # quem usa backend remoto em S3, porque aquele state precisa ser lido tanto
  # do Codespaces (V1) quanto do runner self-hosted (V2).
}
