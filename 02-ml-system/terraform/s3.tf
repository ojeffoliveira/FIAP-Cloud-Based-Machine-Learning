# O bucket pertence ao Terraform - só não através do recurso aws_s3_bucket.
#
# O aws_s3_bucket lê GetBucketObjectLockConfiguration imediatamente depois do
# CreateBucket, e a SCP do AWS Academy nega essa chamada com deny explícito. O
# bucket é criado e o apply falha do mesmo jeito; nenhum bloco lifecycle, versão
# de provider ou -refresh=false pula essa leitura, porque ela acontece dentro do
# Create.
#
# O terraform_data mantém a posse onde o lab precisa: o nome vive no state, todo
# upload abaixo continua esperando o bucket pelo grafo de dependências, e o
# `terraform destroy` remove ele. O que se perde é a detecção de drift - se alguém
# apagar o bucket fora do Terraform, o próximo plan não vai notar.
resource "terraform_data" "bucket" {
  input = local.bucket_name

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      if ! aws s3api head-bucket --bucket ${self.input} 2>/dev/null; then
        aws s3api create-bucket --bucket ${self.input} --region ${var.region} > /dev/null
        aws s3api wait bucket-exists --bucket ${self.input}
      fi

      # o default_tags do provider nunca alcança um bucket que a CLI criou, então o
      # conjunto de tags viaja explicitamente. Tolerante a falha de propósito: se a
      # SCP da conta também negar PutBucketTagging, um bucket sem tag é um desfecho
      # muito melhor para o aluno que um apply reprovado.
      aws s3api put-bucket-tagging \
        --bucket ${self.input} \
        --tagging '${local.bucket_tagging_json}' > /dev/null ||
        echo "aviso: não foi possível aplicar as tags no bucket (provavelmente negado por uma SCP); seguindo" >&2
    EOT
  }

  # Provisioner de destroy não pode referenciar nada além de `self`, e é por isso
  # que o nome do bucket é carregado em `input` em vez de ser recalculado aqui.
  #
  # O `rb --force` esvazia antes de apagar: o model.tar.gz é escrito pelo
  # SageMaker, não por um aws_s3_object, então um DeleteBucket puro falharia num
  # bucket não vazio. A guarda com head-bucket mantém o `make destroy` verde
  # quando o bucket já não existe.
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

resource "aws_s3_bucket_public_access_block" "lab" {
  bucket = terraform_data.bucket.output

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lab" {
  bucket = terraform_data.bucket.output

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Subir pelo Terraform (em vez de `aws s3 cp`) coloca o dado no grafo de
# dependências: o training job não pode começar antes de os bytes que ele lê
# existirem.
resource "aws_s3_object" "train" {
  bucket = terraform_data.bucket.output
  key    = "${local.s3_prefixes.train}/train.csv"
  source = "${local.data_dir}/model_train_headerless.csv"
  etag   = filemd5("${local.data_dir}/model_train_headerless.csv")

  content_type = "text/csv"
}

resource "aws_s3_object" "validation" {
  bucket = terraform_data.bucket.output
  key    = "${local.s3_prefixes.validation}/validation.csv"
  source = "${local.data_dir}/model_validation_headerless.csv"
  etag   = filemd5("${local.data_dir}/model_validation_headerless.csv")

  content_type = "text/csv"
}

# O metadado viaja junto com o dado. Quem auditar o bucket consegue dizer qual
# dataset produziu qual modelo sem ler este repositório.
resource "aws_s3_object" "manifest" {
  bucket = terraform_data.bucket.output
  key    = "${local.s3_prefixes.metadata}/dataset_manifest.json"
  source = "${local.data_dir}/dataset_manifest.json"
  etag   = filemd5("${local.data_dir}/dataset_manifest.json")

  content_type = "application/json"
}

resource "aws_s3_object" "schema" {
  bucket = terraform_data.bucket.output
  key    = "${local.s3_prefixes.metadata}/schema.json"
  source = "${path.module}/../config/schema.json"
  etag   = filemd5("${path.module}/../config/schema.json")

  content_type = "application/json"
}
