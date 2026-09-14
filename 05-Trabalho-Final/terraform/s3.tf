# Bucket único do trabalho final. Dados, artefatos, entrada/saída do Async
# Inference e incidentes de drift compartilham este bucket, diferenciados só
# por prefixo (local.s3_prefixes) — não há motivo de custo ou de segurança
# para separar em vários buckets neste laboratório.
#
# Nunca `aws_s3_bucket` nativo: o provider lê GetBucketObjectLockConfiguration
# imediatamente depois do CreateBucket, e a SCP do AWS Academy nega essa
# chamada com deny explícito. O bucket nasce e o apply falha do mesmo jeito;
# nenhum bloco lifecycle, versão de provider ou -refresh=false pula essa
# leitura, porque ela acontece dentro do próprio Create. Mesmo problema já
# documentado (e contornado) em 02-ml-system, 03-serving-and-scaling e
# 04-ml-operations/01 e /02 deste repositório — `terraform_data` + local-exec
# com guarda `head-bucket` nos dois lados é o único caminho que funciona
# nesta conta.
#
# Nota de adaptação ao CONTRATO.md: o contrato lista o bucket como
# `aws_s3_bucket.lab`. Este arquivo cria `terraform_data.bucket` em vez disso,
# pelo motivo acima — ver PEDIDOS.md. Qualquer outro .tf que precise do nome
# do bucket usa `local.bucket_name` (string), nunca um atributo `.id` de um
# resource `aws_s3_bucket`.
resource "terraform_data" "bucket" {
  input = local.bucket_name

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      if ! aws s3api head-bucket --bucket ${self.input} 2>/dev/null; then
        aws s3api create-bucket --bucket ${self.input} --region ${var.aws_region} > /dev/null
        aws s3api wait bucket-exists --bucket ${self.input}
      fi

      # O default_tags do provider nunca alcança um bucket que a CLI criou,
      # então o conjunto de tags viaja explicitamente. Tolerante a falha de
      # propósito: se a SCP da conta também negar PutBucketTagging, um bucket
      # sem tag é um desfecho muito melhor para o aluno que um apply reprovado.
      aws s3api put-bucket-tagging \
        --bucket ${self.input} \
        --tagging '${local.bucket_tagging_json}' > /dev/null ||
        echo "aviso: não foi possível aplicar as tags no bucket (provavelmente negado por uma SCP); seguindo" >&2
    EOT
  }

  # Provisioner de destroy só pode referenciar `self`, e é por isso que o
  # nome do bucket viaja no `input` em vez de ser recalculado aqui.
  #
  # `rb --force` esvazia antes de apagar: o model.tar.gz do treino, a saída do
  # Async Inference e os JSON de incidente que a Lambda escreve não são
  # geridos por nenhum `aws_s3_object`, então um DeleteBucket simples falharia
  # em bucket não vazio. A guarda head-bucket mantém `make finish`/`destroy`
  # verde quando o bucket já não existe.
  #
  # Mas `rb --force` só apaga a versão corrente de cada chave - num bucket
  # versionado (aws_s3_bucket_versioning.lab abaixo) ele NÃO remove versões
  # antigas nem delete markers, e o DeleteBucket final continua vendo o
  # bucket não vazio. Isso já travou uma execução real de `make finish`: os
  # ~24 recursos de serving/observabilidade caíram em segundos e o destroy
  # ficou preso em `BucketNotEmpty`, exigindo purga manual (list-object-
  # versions + delete-objects em lote) e um segundo destroy para reconciliar
  # o state. Por isso purgamos versões e delete markers em lotes de até 1000
  # (limite do próprio DeleteObjects) antes do `rb`, repetindo até esvaziar -
  # nenhuma chave deste laboratório deveria acumular tantas versões assim,
  # mas o loop paginado é o que garante que um destroy sozinho baste mesmo se
  # acumular.
  provisioner "local-exec" {
    when        = destroy
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      if aws s3api head-bucket --bucket ${self.input} 2>/dev/null; then
        purge_file="$(mktemp)"
        trap 'rm -f "$purge_file"' EXIT

        while true; do
          remaining=$(aws s3api list-object-versions \
            --bucket ${self.input} \
            --max-items 1000 \
            --output text \
            --query 'length([Versions[], DeleteMarkers[]][])')
          if [ "$remaining" -eq 0 ]; then
            break
          fi

          aws s3api list-object-versions \
            --bucket ${self.input} \
            --max-items 1000 \
            --output json \
            --query '{Objects: [Versions[].{Key:Key,VersionId:VersionId}, DeleteMarkers[].{Key:Key,VersionId:VersionId}][]}' \
            > "$purge_file"
          aws s3api delete-objects --bucket ${self.input} --delete "file://$purge_file" > /dev/null
        done

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

# Versionamento, não Object Lock (mesmo motivo do bucket de artifacts do Lab
# 04.2/slm): Object Lock exige `--object-lock-enabled-for-bucket` no
# CreateBucket e a mesma leitura que a SCP nega. Versionamento é o que sobra
# para o bucket não perder silenciosamente um objeto sobrescrito por uma
# reexecução de `make deploy`/`make run` entre um ciclo e o próximo.
resource "aws_s3_bucket_versioning" "lab" {
  bucket = terraform_data.bucket.output

  versioning_configuration {
    status = "Enabled"
  }
}
