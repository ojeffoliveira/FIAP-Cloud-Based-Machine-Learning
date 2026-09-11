# One random suffix per lifecycle, kept in state. SageMaker refuses to reuse a
# job/model/endpoint name that exists in history, so a fresh suffix per
# lifecycle is the minimum non-determinism needed for `make apply` to be
# repeatable without manual edits. Bucket name stays deterministic (account ID
# is already globally unique) so `verify-clean` can find it by prefix alone.
resource "random_id" "lifecycle" {
  byte_length = 4
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  suffix     = random_id.lifecycle.hex

  bucket_name = "${var.project_prefix}-${local.account_id}-${var.region}"

  training_job_name = "${var.project_prefix}-train-${local.suffix}"
  model_name        = "${var.project_prefix}-model-${local.suffix}"

  realtime_endpoint_config_name   = "${var.project_prefix}-rt-epc-${local.suffix}"
  realtime_endpoint_name          = "${var.project_prefix}-rt-${local.suffix}"
  serverless_endpoint_config_name = "${var.project_prefix}-sl-epc-${local.suffix}"
  serverless_endpoint_name        = "${var.project_prefix}-sl-${local.suffix}"
  async_endpoint_config_name      = "${var.project_prefix}-async-epc-${local.suffix}"
  async_endpoint_name             = "${var.project_prefix}-async-${local.suffix}"

  realtime_scaling_policy_name = "${var.project_prefix}-rt-target"
  async_scaling_policy_name    = "${var.project_prefix}-async-target"

  data_dir = "${path.module}/${var.data_dir}"

  s3_prefixes = {
    train        = "input/train"
    validation   = "input/validation"
    output       = "output/training"
    metadata     = "metadata"
    async_output = "async/output"
  }

  train_channel_uri      = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.train}/"
  validation_channel_uri = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.validation}/"
  training_output_uri    = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.output}/"
  async_output_uri       = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.async_output}/"

  realtime_variant_resource_id = "endpoint/${local.realtime_endpoint_name}/variant/AllTraffic"
  async_variant_resource_id    = "endpoint/${local.async_endpoint_name}/variant/AllTraffic"

  # Exact tag set required by the spec - separate from the free-form course
  # tags used elsewhere, kept literal so cost/inventory reports can filter on it.
  tags = {
    Project   = "FIAP-Cloud-Based-Machine-Learning"
    Lab       = "03-serving-and-scaling"
    ManagedBy = "Terraform"
    Owner     = "student"
  }

  # Same tag set in the shape the S3 API expects, for the bucket the CLI creates
  # (see s3.tf) - provider default_tags only reach resources the provider itself
  # creates.
  bucket_tagging_json = jsonencode({
    TagSet = [for k, v in local.tags : { Key = k, Value = v }]
  })
}
