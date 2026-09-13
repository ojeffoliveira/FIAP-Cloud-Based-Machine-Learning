# Bucket de artifacts do SLM: guarda os GGUF publicados pelo A7
# (scripts/model_sync.py) e é o que `ModelDataSource` do SageMaker (A6b) lê
# via S3Prefix.
#
# Mesmo padrão do bucket de state e do Lab 04.1 (D2): nunca `aws_s3_bucket`
# nativo. O provider lê `GetBucketObjectLockConfiguration` imediatamente
# depois do `CreateBucket`, e a SCP do AWS Academy nega essa chamada com deny
# explícito — o bucket nasce e o apply falha do mesmo jeito, sem nenhum
# lifecycle/versão de provider/-refresh=false evitando essa leitura (ela
# acontece dentro do próprio Create). `terraform_data` + `local-exec` com
# guarda `head-bucket` nos dois lados é o único caminho que funciona nesta
# conta.
resource "terraform_data" "artifacts_bucket" {
  input = local.bucket_artifacts_name

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      if ! aws s3api head-bucket --bucket ${self.input} 2>/dev/null; then
        aws s3api create-bucket --bucket ${self.input} --region ${var.region} > /dev/null
        aws s3api wait bucket-exists --bucket ${self.input}
      fi

      # default_tags do provider nunca alcança um bucket que a CLI criou.
      # Tolerante a falha de propósito: se a SCP também negar
      # PutBucketTagging, um bucket sem tag é um desfecho muito melhor que um
      # apply reprovado (mesma decisão do s3.tf do Lab 04.1).
      aws s3api put-bucket-tagging \
        --bucket ${self.input} \
        --tagging '${local.bucket_tagging_json}' > /dev/null ||
        echo "aviso: não foi possível aplicar as tags no bucket de artifacts (provavelmente negado por uma SCP); seguindo" >&2
    EOT
  }

  # O provisioner de destroy só pode referenciar `self` — por isso o nome do
  # bucket viaja no `input` em vez de ser recalculado aqui. `rb --force`
  # esvazia antes de apagar: os GGUF que o A7 sincroniza não são
  # `aws_s3_object` geridos pelo Terraform, então um `DeleteBucket` simples
  # falharia em bucket não vazio.
  provisioner "local-exec" {
    when        = destroy
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      if aws s3api head-bucket --bucket ${self.input} 2>/dev/null; then
        aws s3 rb s3://${self.input} --force
      fi
    EOT
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = terraform_data.artifacts_bucket.output

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = terraform_data.artifacts_bucket.output

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Versionamento, não Object Lock: Object Lock exige a flag
# `--object-lock-enabled-for-bucket` no CreateBucket e o mesmo
# `GetBucketObjectLockConfiguration` que a SCP nega — inviável nesta conta.
# Versionamento é o que sobra para tornar cada prefixo por release
# "imutável na prática": o layout D7 já garante um prefixo por
# revisão+release (nunca sobrescrito por outra quantização), e o
# versionamento garante que mesmo uma reexecução acidental de
# `model_sync.py` sobre a MESMA key preserva a versão anterior em vez de
# perdê-la silenciosamente.
resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = terraform_data.artifacts_bucket.output

  versioning_configuration {
    status = "Enabled"
  }
}
