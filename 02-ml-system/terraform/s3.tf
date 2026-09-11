# The bucket belongs to Terraform - just not through the aws_s3_bucket resource.
#
# aws_s3_bucket reads GetBucketObjectLockConfiguration immediately after
# CreateBucket, and the AWS Academy SCP denies that call with an explicit deny.
# The bucket is created and the apply fails anyway; no lifecycle block, provider
# version or -refresh=false skips that read, because it happens inside Create.
#
# terraform_data keeps ownership where the lab needs it: the name lives in state,
# every upload below still waits for the bucket through the dependency graph, and
# `terraform destroy` removes it. What is lost is drift detection - if someone
# deletes the bucket outside Terraform, the next plan will not notice.
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

      # provider default_tags never reach a bucket the CLI created, so the tag set
      # travels explicitly. Best effort on purpose: if the account's SCP also denies
      # PutBucketTagging, an untagged bucket is a far better outcome for the student
      # than a failed apply.
      aws s3api put-bucket-tagging \
        --bucket ${self.input} \
        --tagging '${local.bucket_tagging_json}' > /dev/null ||
        echo "warning: could not tag the bucket (likely denied by an SCP); continuing" >&2
    EOT
  }

  # Destroy-time provisioners may reference nothing but `self`, which is why the
  # bucket name is carried in `input` instead of being recomputed here.
  #
  # `rb --force` empties before deleting: model.tar.gz is written by SageMaker,
  # not by any aws_s3_object, so a plain DeleteBucket would fail on a non-empty
  # bucket. The head-bucket guard keeps `make destroy` green when the bucket is
  # already gone.
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

# Uploading through Terraform (instead of `aws s3 cp`) puts the data in the
# dependency graph: the training job cannot start before the bytes it reads exist.
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

# Metadata travels with the data. Anyone auditing the bucket can tell which
# dataset produced which model without reading this repository.
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
