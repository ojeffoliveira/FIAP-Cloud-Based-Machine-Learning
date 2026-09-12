# EventBridge — a ponte entre "o alarme mudou de estado" e "alguém faz algo".
#
# O padrão do evento é estreito de propósito. Um filtro largo (só source
# aws.cloudwatch) faria esta Lambda acordar para qualquer alarme da conta, inclusive
# alarmes de outros labs rodando na mesma conta de turma. Os quatro campos abaixo
# amarram a regra a exatamente um alarme e exatamente uma transição.
resource "aws_cloudwatch_event_rule" "drift_alarm" {
  count = var.deploy_serving ? 1 : 0

  name        = local.event_rule_name
  description = "Encaminha para a Lambda de reação a entrada em ALARM do alarme de drift deste lab."

  event_pattern = jsonencode({
    source        = ["aws.cloudwatch"]
    "detail-type" = ["CloudWatch Alarm State Change"]
    resources     = [aws_cloudwatch_metric_alarm.data_drift[0].arn]
    detail = {
      state = {
        value = ["ALARM"]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "drift_response" {
  count = var.deploy_serving ? 1 : 0

  rule      = aws_cloudwatch_event_rule.drift_alarm[0].name
  target_id = "drift-response-lambda"
  arn       = aws_lambda_function.drift_response[0].arn
}
