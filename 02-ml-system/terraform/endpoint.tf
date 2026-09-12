# Endpoint em tempo real. Este recurso ESPERA pelo InService no provider 6.60.0
# (waitEndpointInService), então um apply bem-sucedido aqui significa que a
# capacidade está de fato alcançável - diferente do recurso de training job.
#
# Este é o único recurso cobrado continuamente no lab. O `make destroy` e o
# `make verify-clean` existem por causa desta linha.
resource "aws_sagemaker_endpoint" "churn" {
  count = var.deploy_serving ? 1 : 0

  name                 = local.endpoint_name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.churn[0].name
}
