data "aws_caller_identity" "current" {}

# A Canonical só publica o parâmetro público de AMI para volume gp2 nesta
# geração (22.04) — provado por A3 na conta real: o path .../ebs-gp3/ami-id
# não existe (ParameterNotFound). O volume raiz continua gp3 porque isso é
# decidido no root_block_device do aws_instance (ec2.tf), independente do
# volume da AMI de origem.
data "aws_ssm_parameter" "ubuntu_2204_ami" {
  name = "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
}

data "aws_vpc" "default" {
  default = true
}

# Uma sub-rede default qualquer com IP público automático — o runner não
# precisa de HA nem de escolha fina de AZ, só precisa existir dentro da VPC
# default que o Academy já provisiona.
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}
