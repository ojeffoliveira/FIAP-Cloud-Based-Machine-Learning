# Asynchronous Inference: requisicao e resposta desacopladas pelo S3. A
# entrada chega via S3 (upload feito por src/final_project/serving.py) e a
# saida cai em local.async_output_uri (prefixo ja resolvido por A0). A
# capacidade pode ir a zero entre requisicoes -- ver autoscaling.tf.
#
# Deliberadamente sem `notification_config` (SNS): o ACADEMY.md confirma que
# nenhum lab anterior deste repositorio usa SNS aqui -- o consumo e por
# polling direto do objeto de saida no S3 (serving.py faz esse polling),
# mesmo desenho do lab 03 (03-serving-and-scaling/terraform/async.tf).
resource "aws_sagemaker_endpoint_configuration" "async" {
  count = var.enable_serving ? 1 : 0

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
  count = var.enable_serving ? 1 : 0

  name                 = local.async_endpoint_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.async[0].name
}
