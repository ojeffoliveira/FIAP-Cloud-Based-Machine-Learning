# EventBridge — a ponte entre "o alarme mudou de estado" e "alguém faz algo".
#
# Uma regra só, cobrindo os dois alarmes (data_drift e prediction_drift): o
# campo `resources` do padrão de evento é uma lista, e a semântica de lista
# no filtro do EventBridge é OU — a regra dispara se o alarme que mudou de
# estado for qualquer um dos dois ARNs, sem precisar de uma segunda regra e
# uma segunda permissão de invoke para a mesma Lambda. A Lambda por trás
# recebe o nome do alarme dentro do próprio evento e decide lá qual dos dois
# incidentes está registrando (drift_response.py:validar_evento).
#
# O filtro é estreito de propósito, do mesmo jeito que em 04-ml-operations/01:
# só source aws.cloudwatch não bastaria, porque a conta de turma roda outros
# labs com seus próprios alarmes. Os quatro campos abaixo amarram a regra a
# exatamente estes dois alarmes e exatamente à transição para ALARM.
resource "aws_cloudwatch_event_rule" "drift_alarm" {
  count = var.enable_serving ? 1 : 0

  name        = local.event_rule_name
  description = "Encaminha para a Lambda de reação a entrada em ALARM de qualquer um dos dois alarmes de drift deste trabalho final."

  event_pattern = jsonencode({
    source        = ["aws.cloudwatch"]
    "detail-type" = ["CloudWatch Alarm State Change"]
    resources = [
      aws_cloudwatch_metric_alarm.data_drift[0].arn,
      aws_cloudwatch_metric_alarm.prediction_drift[0].arn,
    ]
    detail = {
      state = {
        value = ["ALARM"]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "drift_response" {
  count = var.enable_serving ? 1 : 0

  rule      = aws_cloudwatch_event_rule.drift_alarm[0].name
  target_id = "drift-response-lambda"
  arn       = aws_lambda_function.drift_response[0].arn
}
