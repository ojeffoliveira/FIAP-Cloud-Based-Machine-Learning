# Asynchronous Inference: requisição e resposta desacopladas pelo S3. A entrada
# chega via S3 (enviada por scripts/lab.py async) e a saída cai em
# local.async_output_uri. A capacidade pode ir a zero entre requisições.
resource "aws_sagemaker_endpoint_configuration" "async" {
  count = var.deploy_serving ? 1 : 0

  name = local.async_endpoint_config_name

  production_variants {
    variant_name           = "AllTraffic"
    model_name             = aws_sagemaker_model.churn[0].name
    initial_instance_count = 1
    instance_type          = var.instance_type
    initial_variant_weight = 1
  }

  async_inference_config {
    output_config {
      s3_output_path = local.async_output_uri
    }

    client_config {
      max_concurrent_invocations_per_instance = var.async_max_concurrent_invocations_per_instance
    }
  }
}

resource "aws_sagemaker_endpoint" "async" {
  count = var.deploy_serving ? 1 : 0

  name                 = local.async_endpoint_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.async[0].name
}
