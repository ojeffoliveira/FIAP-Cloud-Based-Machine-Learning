data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Role já provisionada pelo AWS Academy. O trabalho final nunca cria role ou
# policy de IAM — é uma restrição não negociável da conta do Academy.
data "aws_iam_role" "lab" {
  name = var.execution_role_name
}
