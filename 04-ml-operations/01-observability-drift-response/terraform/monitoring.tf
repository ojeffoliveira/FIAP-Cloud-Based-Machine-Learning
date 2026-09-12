# Alarme de drift de dados — a regra que transforma observação em incidente.
#
# Escolhas que a aula precisa saber que foram escolhas, não padrão:
#
# * **Dimensão só EndpointName.** O Python publica DataDriftPSIMax duas vezes: uma
#   com [EndpointName] e outra com [EndpointName, Window]. No CloudWatch, um
#   conjunto de dimensões diferente é uma MÉTRICA diferente, não um filtro da mesma
#   série. O alarme observa a versão sem Window; o dashboard usa a versão com
#   Window para separar baseline de drift. Se o alarme apontasse para a série com
#   Window, ele nunca sairia de INSUFFICIENT_DATA.
#
# * **1 datapoint em 1 período de 60 s.** Uma aula não pode esperar uma janela
#   estatística longa. O preço é sensibilidade a um ponto único — aceitável aqui
#   porque cada janela publica exatamente um valor, e é dito no README.
#
# * **treat_missing_data = notBreaching.** Antes do primeiro `make baseline` não
#   existe datapoint. Sem isso o alarme abriria em INSUFFICIENT_DATA, e o aluno não
#   distinguiria "ainda não mediu" de "mediu e está ruim".
resource "aws_cloudwatch_metric_alarm" "data_drift" {
  count = var.deploy_serving ? 1 : 0

  alarm_name          = local.alarm_name
  alarm_description   = "PSI máximo entre as features passou de ${var.drift_alarm_threshold}: a distribuição de entrada saiu do mundo em que churn-v1 foi treinado. Sinal para investigar, não prova de queda de qualidade."
  namespace           = var.metrics_namespace
  metric_name         = "DataDriftPSIMax"
  statistic           = "Maximum"
  period              = var.alarm_period_seconds
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = var.drift_alarm_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    EndpointName = local.endpoint_name
  }

  # Nenhuma ação de alarme aqui de propósito: o alarme não chama a Lambda
  # diretamente. Quem faz a ponte é o EventBridge, ouvindo a mudança de estado —
  # veja eventbridge.tf. É um acoplamento mais frouxo e é o padrão que a AWS
  # documenta para reagir a alarme com lógica própria.
}
