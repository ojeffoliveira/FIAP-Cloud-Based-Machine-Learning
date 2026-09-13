# Somente identificadores não secretos. Nenhuma credencial passa por aqui.

output "instance_id" {
  description = "ID da instância EC2 do runner."
  value       = aws_instance.runner.id
}

output "availability_zone" {
  description = "AZ onde o runner foi lançado."
  value       = aws_instance.runner.availability_zone
}

output "public_ip" {
  description = "IP público do runner. Não abrimos nenhuma porta inbound para ele; serve só de referência (ex.: describe-instances)."
  value       = aws_instance.runner.public_ip
}

output "security_group_id" {
  description = "Security group do runner — auditar aqui para provar ingress=0."
  value       = aws_security_group.runner.id
}

output "security_group_name" {
  description = "Nome do security group do runner."
  value       = aws_security_group.runner.name
}

output "iam_instance_profile" {
  description = "Instance profile anexado — deve ser sempre LabInstanceProfile."
  value       = var.instance_profile_name
}

output "ssm_start_session_command" {
  description = "Comando pronto para abrir uma sessão SSM na instância e rodar scripts/register_runner.sh."
  value       = "aws ssm start-session --target ${aws_instance.runner.id} --region ${var.region}"
}

output "github_new_runner_url" {
  description = "Página do GitHub onde o aluno copia o token de registro (Settings > Actions > Runners > New self-hosted runner)."
  value       = local.github_new_runner_url
}

output "github_runners_url" {
  description = "Página do GitHub para confirmar o status Idle/Online do runner depois do registro."
  value       = local.github_runners_url
}

# --------------------------------------------------------------------------- #
# Registro automático via SSM Parameter Store (decisão D44). O NOME do
# parâmetro não é segredo — só o valor é, e o valor nunca passa por aqui
# (nenhum output ou data source deste stack lê o valor). Rode
# `make runner-token` antes de `make runner-apply` para gravar o valor.
# --------------------------------------------------------------------------- #

output "runner_token_ssm_parameter" {
  description = "Nome do parâmetro SecureString que `make runner-token` grava e o user-data lê/apaga no boot. Nunca o valor."
  value       = var.runner_token_ssm_parameter
}

output "runner_token_ssm_check_command" {
  description = <<-EOT
    Comando pronto para confirmar SE o parâmetro existe e qual o TIPO dele
    (SecureString), sem nunca revelar o valor. Antes do registro automático:
    "SecureString". Depois do registro automático bem-sucedido: o comando
    falha com ParameterNotFound — o parâmetro foi apagado de propósito.
  EOT
  value       = "aws ssm get-parameter --name ${var.runner_token_ssm_parameter} --query 'Parameter.Type' --output text"
}
