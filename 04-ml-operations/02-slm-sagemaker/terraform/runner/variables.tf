variable "region" {
  description = "Região da AWS. O Academy Learner Lab só permite us-east-1."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.region == "us-east-1"
    error_message = "Este lab é validado só em us-east-1, a região que o AWS Academy permite."
  }
}

variable "project_prefix" {
  description = "Prefixo base de nomes do Lab 04.2 (blueprint da spec, seção 1)."
  type        = string
  default     = "fiap-cbml-42"

  validation {
    condition     = can(regex("^[a-z0-9-]{3,32}$", var.project_prefix))
    error_message = "O prefixo precisa ser letras minúsculas, dígitos e hifens."
  }
}

variable "instance_profile_name" {
  description = "Instance profile já provisionado pelo Academy. O lab não tem permissão para criar role/instance profile de IAM."
  type        = string
  default     = "LabInstanceProfile"
}

variable "github_repository_owner" {
  description = "Owner do repositório GitHub público onde o runner é registrado."
  type        = string
  default     = "vamperst"
}

variable "github_repository_name" {
  description = "Nome do repositório GitHub público onde o runner é registrado."
  type        = string
  default     = "FIAP-Cloud-Based-Machine-Learning"
}

variable "instance_type" {
  description = <<-EOT
    Tipo de instância do runner. t3.medium é o baseline da spec (04_AWS_ACADEMY_GUARDRAILS.md)
    e foi confirmado disponível na AZ usada no teste real do agente A3.
  EOT
  type        = string
  default     = "t3.medium"
}

variable "root_volume_size_gb" {
  description = "Volume raiz gp3 do runner. 30 GB é o tamanho da spec; o guardrail proíbe passar de 100 GB."
  type        = number
  default     = 30

  validation {
    condition     = var.root_volume_size_gb > 0 && var.root_volume_size_gb <= 100
    error_message = "O guardrail do Academy proíbe volume de runner acima de 100 GB."
  }
}

# --------------------------------------------------------------------------- #
# Versões pinadas do software instalado pelo user-data — cada uma com o
# checksum publicado pelo próprio fornecedor, conferido à mão antes de travar
# aqui (ver comentário de cada verificação em templates/user_data.sh.tftpl).
# Subir a versão exige repetir a conferência do checksum, não só editar o
# número.
# --------------------------------------------------------------------------- #

variable "terraform_version" {
  description = "Versão do Terraform instalada no runner. Igual à required_version deste stack, para o runner falar a mesma versão que o Codespaces."
  type        = string
  default     = "1.15.8"
}

variable "terraform_linux_amd64_sha256" {
  description = <<-EOT
    SHA-256 de terraform_<versão>_linux_amd64.zip, copiado de
    https://releases.hashicorp.com/terraform/<versão>/terraform_<versão>_SHA256SUMS
    e conferido por download real antes de travar aqui.
  EOT
  type        = string
  default     = "d25ce7b6902013ad905db3d2eab0be4cd905887fe88b81a6171b8d5503c31f3d"
}

variable "github_actions_runner_version" {
  description = "Versão do actions/runner instalada em /opt/actions-runner (sem registrar)."
  type        = string
  default     = "2.337.0"
}

variable "github_actions_runner_linux_x64_sha256" {
  description = <<-EOT
    SHA-256 de actions-runner-linux-x64-<versão>.tar.gz, publicado no `digest` do
    asset da release em https://api.github.com/repos/actions/runner/releases/latest
    e conferido por download real antes de travar aqui.
  EOT
  type        = string
  default     = "70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613"
}

variable "jq_version" {
  description = <<-EOT
    Versão do jq instalada como binário estático em /usr/local/bin/jq. Decisão
    D8 do coordenador: o SG deste runner só abre egress 443 (nunca 80), então
    jq não vem de apt (que exigiria os mirrors HTTP do Ubuntu) — vem do
    binário oficial linux-amd64 publicado na release do próprio projeto
    (github.com/jqlang/jq), único pacote do user-data sem alternativa nativa
    já presente na AMI (git, gpg e ca-certificates já vêm de fábrica; unzip é
    substituído por `python3 -m zipfile`).
  EOT
  type        = string
  default     = "1.8.2"
}

variable "jq_linux_amd64_sha256" {
  description = <<-EOT
    SHA-256 de jq-linux-amd64, copiado do sha256sum.txt publicado na própria
    release (github.com/jqlang/jq/releases/download/jq-<versão>/sha256sum.txt)
    e conferido por download real antes de travar aqui.
  EOT
  type        = string
  default     = "b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f"
}

variable "aws_cli_gpg_public_key" {
  description = <<-EOT
    Chave pública GPG "AWS CLI Team <aws-cli@amazon.com>", fingerprint
    FB5D B77F D5C1 18B8 0511 ADA8 A631 0ACC 4672 475C, copiada do guia oficial
    de instalação da AWS CLI v2 (docs.aws.amazon.com/cli/latest/userguide/
    getting-started-install.html). Usada pelo user-data para verificar a
    assinatura .sig do zip da CLI — a AWS não publica um SHA-256 estático para
    o bundle "current" (ele muda a cada patch sem versão fixa na URL); a
    assinatura GPG é o mecanismo de integridade que a própria AWS recomenda
    para este artefato.
  EOT
  type        = string
  default     = <<-EOT
    -----BEGIN PGP PUBLIC KEY BLOCK-----

    mQINBF2Cr7UBEADJZHcgusOJl7ENSyumXh85z0TRV0xJorM2B/JL0kHOyigQluUG
    ZMLhENaG0bYatdrKP+3H91lvK050pXwnO/R7fB/FSTouki4ciIx5OuLlnJZIxSzx
    PqGl0mkxImLNbGWoi6Lto0LYxqHN2iQtzlwTVmq9733zd3XfcXrZ3+LblHAgEt5G
    TfNxEKJ8soPLyWmwDH6HWCnjZ/aIQRBTIQ05uVeEoYxSh6wOai7ss/KveoSNBbYz
    gbdzoqI2Y8cgH2nbfgp3DSasaLZEdCSsIsK1u05CinE7k2qZ7KgKAUIcT/cR/grk
    C6VwsnDU0OUCideXcQ8WeHutqvgZH1JgKDbznoIzeQHJD238GEu+eKhRHcz8/jeG
    94zkcgJOz3KbZGYMiTh277Fvj9zzvZsbMBCedV1BTg3TqgvdX4bdkhf5cH+7NtWO
    lrFj6UwAsGukBTAOxC0l/dnSmZhJ7Z1KmEWilro/gOrjtOxqRQutlIqG22TaqoPG
    fYVN+en3Zwbt97kcgZDwqbuykNt64oZWc4XKCa3mprEGC3IbJTBFqglXmZ7l9ywG
    EEUJYOlb2XrSuPWml39beWdKM8kzr1OjnlOm6+lpTRCBfo0wa9F8YZRhHPAkwKkX
    XDeOGpWRj4ohOx0d2GWkyV5xyN14p2tQOCdOODmz80yUTgRpPVQUtOEhXQARAQAB
    tCFBV1MgQ0xJIFRlYW0gPGF3cy1jbGlAYW1hem9uLmNvbT6JAlQEEwEIAD4CGwMF
    CwkIBwIGFQoJCAsCBBYCAwECHgECF4AWIQT7Xbd/1cEYuAURraimMQrMRnJHXAUC
    akV0ygUJDqP4lQAKCRCmMQrMRnJHXFHjD/9eyZLYcKuQOlLvtqSDtUBiEZf6ZZjM
    i3ygYH8rJNtuToUH+HvSpe819urJCquXhDrlK6N+aqW0hCLtNABJG/vsafIgvIYJ
    hSGgpgtNnQyMV1jViRWqPjbouw8OkYKBThUfT1i2Y+wn58ifs6ODBCmTexWtXspA
    Si+Gt49xDOW0APmbOPnI+a4HJW6tVEo6MWS0WjzpiBayR3d1A4pt4YrPfSdDgpLo
    h2SLQqlRqvvVZJaWBjhkErNFpfsBA06sDcPEOb0G8LBUbR4WOcdvhe5LubJbZuxC
    AG9kNPCVeQP1ixwjgjXKysaxeQ6rv0VzIQgRp6tLVLWhy6AKDNvLjFSsmXZ1Wl08
    Y/RlOHXlzLuQMRE6sR1wOdRxc9TsrNWTGiBK65cvSWOy03JeBkQQ8pesqltiyxI9
    U21kkgiXtTSKNGfKK8pO27D81YANhRqPK7iTp6kuFiY2WtOg90KTMNlIT+Ff85Y2
    b1rHj6Z0SrCkJujhWk3IBPic/wJgz01LEc/OAdUPlby90RJZcIBhSlWhT7mXnXIO
    c0HWlNQrns2s3CTyYwZSiSlYe9ApeLwhjDo8NhbFuCAy61l6O5UsR4AfZxx/rGKv
    2wFb1/RN/P4gNe6vmxZAPjR0AQcwD3tc2McimOLr/22kmPz8IH3I0X7WoSFr0Biz
    E91G7bb0hOb/cA==
    =knv7
    -----END PGP PUBLIC KEY BLOCK-----
  EOT
}
