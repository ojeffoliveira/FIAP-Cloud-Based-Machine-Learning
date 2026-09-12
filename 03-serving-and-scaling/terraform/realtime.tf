# Real-Time Endpoint: instância persistente, latência previsível, cobra 24/7.
# Este recurso ESPERA pelo InService no provider 6.60.0, então um apply
# bem-sucedido aqui significa que o endpoint está de fato alcançável.
resource "aws_sagemaker_endpoint_configuration" "realtime" {
  count = var.deploy_serving ? 1 : 0

  name = local.realtime_endpoint_config_name

  production_variants {
    variant_name           = "AllTraffic"
    model_name             = aws_sagemaker_model.churn[0].name
    initial_instance_count = 1
    instance_type          = var.instance_type
    initial_variant_weight = 1
  }
}

resource "aws_sagemaker_endpoint" "realtime" {
  count = var.deploy_serving ? 1 : 0

  name                 = local.realtime_endpoint_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.realtime[0].name
}
