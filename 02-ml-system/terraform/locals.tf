# One random suffix per lifecycle, kept in state.
#
# The bucket name stays deterministic (account ID is already globally unique),
# but SageMaker refuses to reuse a training job name that exists in history -
# including a completed job from a previous `make e2e`. Without a per-lifecycle
# suffix the second run of the lab would fail on ResourceInUse, so the suffix is
# the minimum non-determinism required for the lab to be repeatable with no
# manual editing. It is generated once, persisted in state, and only changes
# after a destroy.
resource "random_id" "lifecycle" {
  byte_length = 4
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  suffix     = random_id.lifecycle.hex

  bucket_name          = "${var.project_prefix}-${local.account_id}"
  training_job_name    = "${var.project_prefix}-train-${local.suffix}"
  model_name           = "${var.project_prefix}-model-${local.suffix}"
  endpoint_config_name = "${var.project_prefix}-epc-${local.suffix}"
  endpoint_name        = "${var.project_prefix}-ep-${local.suffix}"

  data_dir = "${path.module}/${var.data_dir}"

  s3_prefixes = {
    train      = "input/train"
    validation = "input/validation"
    output     = "output/training"
    metadata   = "metadata"
  }

  train_channel_uri      = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.train}/"
  validation_channel_uri = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.validation}/"
  training_output_uri    = "s3://${terraform_data.bucket.output}/${local.s3_prefixes.output}/"

  # No personal data in tags: they end up in cost reports the whole class shares.
  tags = {
    course     = "cloud-based-machine-learning"
    lab        = "lab1-model-to-ml-system"
    purpose    = "education"
    managed_by = "terraform"
  }

  # Same tag set in the shape the S3 API expects, for the bucket the CLI creates
  # (see s3.tf) - provider default_tags only reach resources the provider itself
  # creates.
  bucket_tagging_json = jsonencode({
    TagSet = [for k, v in local.tags : { Key = k, Value = v }]
  })
}
