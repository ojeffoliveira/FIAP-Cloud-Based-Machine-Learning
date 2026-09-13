# Security group do runner self-hosted — zero regras inbound (regra dura do
# lab). O runner só inicia conexões de saída para o GitHub; administração é
# 100% via SSM Session Manager, que não depende de porta inbound nenhuma.
# Nunca SSH 22, nem "para facilitar".
resource "aws_security_group" "runner" {
  name        = local.sg_name
  description = "Runner self-hosted do GitHub Actions (Lab 04.2) - sem ingress"
  vpc_id      = data.aws_vpc.default.id

  # Zera o egress "allow all" que a API EC2 cria por padrão em toda
  # CreateSecurityGroup. A única regra de saída é 443 (decisão D8) — sem 80,
  # mesmo o A3 tendo provado que o apt padrão exige 80: o user-data resolve
  # isso evitando apt-get, não abrindo porta por conveniência.
  egress = []

  tags = merge(local.tags, { Name = local.sg_name })
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.runner.id
  description       = <<-EOT
    HTTPS apenas (decisão D8 do coordenador). O A3 provou (runner-provado.md)
    que os mirrors padrão do apt (*.ec2.archive.ubuntu.com, security.ubuntu.com)
    só servem HTTP — mas a resposta a isso NÃO é abrir a porta 80. É o
    user-data evitar apt-get e instalar cada dependência via download HTTPS
    oficial com checksum/assinatura verificados (AWS CLI, Terraform, runner do
    GitHub, jq), exatamente como já fazíamos para os três primeiros. Ver
    templates/user_data.sh.tftpl para o "porquê" de cada item.
  EOT
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_instance" "runner" {
  #checkov:skip=CKV_AWS_88:IP público é obrigatório aqui — as regras duras do lab proíbem
  # NAT Gateway e VPC endpoint (custo/complexidade fora do escopo didático), e a subnet
  # default não tem outra rota de saída para a internet; sem IP público a instância não
  # alcança GitHub/HashiCorp/AWS pela IGW e o user-data trava.
  #checkov:skip=CKV_AWS_46:falso positivo, mesma natureza do finding do gitleaks em
  # locals.tf — o user_data só carrega checksums SHA-256 e a chave pública GPG da AWS
  # (decisão D8), dados públicos usados para verificar assinatura/integridade de
  # download, nunca um segredo. O token de registro do runner nunca passa por aqui —
  # é digitado interativamente em scripts/register_runner.sh, na própria instância.
  #checkov:skip=CKV_AWS_126:monitoramento detalhado (granularidade de 1 min) é custo
  # extra sem valor pedagógico para um runner efêmero de CI de laboratório; a métrica
  # padrão de 5 min já é suficiente para depurar o user-data via SSM/CloudWatch Logs.
  ami                         = data.aws_ssm_parameter.ubuntu_2204_ami.value
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnets.default.ids[0]
  vpc_security_group_ids      = [aws_security_group.runner.id]
  iam_instance_profile        = var.instance_profile_name
  associate_public_ip_address = true
  # t3.medium já é EBS-optimized por padrão desde essa geração de instância (sem custo
  # adicional); declarar explícito só documenta a garantia e silencia o CKV_AWS_135.
  ebs_optimized = true

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size_gb
    delete_on_termination = true
    encrypted             = true
  }

  # IMDSv2 obrigatório (regra dura do lab): token required, hop limit 1 —
  # bloqueia SSRF via qualquer proxy/container que precisasse de mais de um
  # hop para chegar ao metadata endpoint.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  user_data                   = local.user_data
  user_data_replace_on_change = true

  tags = merge(local.tags, { Name = local.instance_name })
}
