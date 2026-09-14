# Alarmes de drift — a regra que transforma observação em incidente.
#
# Decisão de desenho que diverge do molde de 04-ml-operations/01 (registrada em
# PEDIDOS.md para A6b, dono de src/final_project/monitoring.py): lá existe um
# único endpoint, e o alarme filtra por dimensão EndpointName. Aqui o Trabalho
# Final sobe Real-Time + Serverless + Async do MESMO modelo ao mesmo tempo
# (ACADEMY.md item 13), e o pattern em produção é escolha do aluno em
# student/solution.yaml (Regra 4 do CONTRATO: nenhum arquivo pode sugerir qual
# escolher). Um alarme dimensionado por EndpointName obrigaria a fixar aqui,
# em Terraform, qual dos três endpoints é "o de produção" — isso decidiria o
# pattern por trás do aluno. A saída é o alarme observar a métrica SEM
# dimensão nenhuma (o agregado que monitoring.py publica uma vez por rodada,
# independente de qual endpoint recebeu a chamada), e o dashboard.tf usar a
# dimensão Window (baseline/shifted) só para contar a história pedagógica —
# a mesma separação de responsabilidade do molde, troca só o rótulo da
# dimensão que o alarme ignora.
#
# * 1 datapoint em 1 período: cada rodada de drift.py publica um valor só;
#   esperar uma janela estatística longa não cabe numa aula.
# * treat_missing_data = notBreaching: antes do primeiro `make baseline` não
#   existe datapoint, e o aluno precisa distinguir "ainda não mediu" de
#   "mediu e está ruim".
# * threshold = var.psi_threshold (0.20): o mesmo número que
#   config/scenario.yaml expõe para o lado Python — um só limiar, nunca dois
#   sincronizados à mão.

resource "aws_cloudwatch_metric_alarm" "data_drift" {
  count = var.enable_serving ? 1 : 0

  alarm_name          = local.data_drift_alarm_name
  alarm_description   = "PSI máximo entre as features passou de ${var.psi_threshold}: a distribuição de entrada saiu do mundo em que churn-v1 foi treinado. Sinal para investigar, não prova de queda de qualidade."
  namespace           = var.metrics_namespace
  metric_name         = "DataDriftPSIMax"
  statistic           = "Maximum"
  period              = var.alarm_period_seconds
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = var.psi_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  # Sem alarm_actions de propósito: quem faz a ponte até a Lambda é o
  # EventBridge, ouvindo a mudança de estado (eventbridge.tf) — acoplamento
  # mais frouxo, padrão que a AWS documenta para reagir a alarme com lógica
  # própria em vez de SNS.
}

resource "aws_cloudwatch_metric_alarm" "prediction_drift" {
  count = var.enable_serving ? 1 : 0

  alarm_name          = local.prediction_drift_alarm_name
  alarm_description   = "PSI da distribuição de predições passou de ${var.psi_threshold}: o modelo está mudando de opinião sobre a base, mesmo antes de qualquer ground truth confirmar erro."
  namespace           = var.metrics_namespace
  metric_name         = "PredictionDriftPSI"
  statistic           = "Maximum"
  period              = var.alarm_period_seconds
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = var.psi_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
}
