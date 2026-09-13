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
