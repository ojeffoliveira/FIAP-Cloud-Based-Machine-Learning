# Serverless Inference: mesmo artefato e mesma imagem, sem instância persistente.
# A AWS gerencia a capacidade; o lab mede (não promete) o comportamento da
# primeira chamada / partida a frio no `make compare`.
resource "aws_sagemaker_endpoint_configuration" "serverless" {
  count = var.deploy_serving ? 1 : 0

  name = local.serverless_endpoint_config_name

  production_variants {
    variant_name = "AllTraffic"
    model_name   = aws_sagemaker_model.churn[0].name

    serverless_config {
      max_concurrency   = var.serverless_max_concurrency
      memory_size_in_mb = var.serverless_memory_size_in_mb
    }
  }
}

resource "aws_sagemaker_endpoint" "serverless" {
  count = var.deploy_serving ? 1 : 0

  name                 = local.serverless_endpoint_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.serverless[0].name
}
