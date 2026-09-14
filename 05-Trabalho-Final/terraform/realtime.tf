# Real-Time Endpoint: instancia persistente (var.instance_type), latencia
# previsivel, cobra por hora enquanto existir. Este recurso espera pelo
# InService no provider 6.60.0 -- um apply bem-sucedido aqui significa que o
# endpoint ja esta de fato alcancavel. Mesmo desenho comprovado no lab 03
# (03-serving-and-scaling/terraform/realtime.tf) -- ver ACADEMY.md itens 3 e 11.
resource "aws_sagemaker_endpoint_configuration" "realtime" {
  count = var.enable_serving ? 1 : 0

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
  count = var.enable_serving ? 1 : 0

  name                 = local.realtime_endpoint_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.realtime[0].name
}
