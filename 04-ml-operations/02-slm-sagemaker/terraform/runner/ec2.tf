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

  # BUG REAL encontrado em execução (não hipotético): misturar o atributo
  # legado "egress" aqui com o recurso separado aws_vpc_security_group_egress_rule
  # abaixo faz o provider tratar os dois como fontes de verdade conflitantes —
  # todo apply deste recurso reconcilia a SG contra "egress = []" e REVOGA a
  # regra de 443 criada pelo outro recurso. `ignore_changes` trava o legado no
  # valor já aplicado e devolve a autoridade exclusiva para o recurso dedicado.
  lifecycle {
    ignore_changes = [egress]
  }

  tags = merge(local.tags, { Name = local.sg_name })
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.runner.id
  # NOTA (fix real, nao cosmetico): a descricao de regra de SG na API EC2
  # aceita só o charset a-zA-Z0-9. _-:/()#,@[]+=&;{}!$* (sem acento, sem
  # aspas, menos de 256 chars) — o texto original em PT-BR acentuado violava
  # isso e o apply falhava com InvalidParameterValue. Reescrito sem acentos.
  description = "HTTPS apenas (decisao D8). Mirrors do apt exigem porta 80, mas a solucao e evitar apt-get: user-data baixa cada dependencia via HTTPS oficial com checksum/assinatura verificados. Ver templates/user_data.sh.tftpl."
  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443
  cidr_ipv4   = "0.0.0.0/0"
}

resource "aws_instance" "runner" {
  #checkov:skip=CKV_AWS_88:IP público é obrigatório aqui — as regras duras do lab proíbem
  # NAT Gateway e VPC endpoint (custo/complexidade fora do escopo didático), e a subnet
  # default não tem outra rota de saída para a internet; sem IP público a instância não
  # alcança GitHub/HashiCorp/AWS pela IGW e o user-data trava.
  #checkov:skip=CKV_AWS_46:falso positivo, mesma natureza do finding do gitleaks logo
  # abaixo (aws_cli_gpg_key_fingerprint) — o user_data só carrega checksums SHA-256, a
  # chave pública GPG da AWS (decisão D8) e o NOME (nunca o valor) do parâmetro SSM do
  # token de registro (decisão D44). Nome de parâmetro não é segredo. O valor do token
  # nunca passa por aqui nem por nenhum outro arquivo .tf deste stack — só a instância,
  # via AWS CLI dentro do próprio user-data, lê e apaga o valor; o Terraform nunca
  # declara `data "aws_ssm_parameter"` para este nome (isso vazaria o valor no state).
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
