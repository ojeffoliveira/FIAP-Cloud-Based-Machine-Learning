# Lambda de reação — o que o sistema faz sozinho quando o alarme dispara.
#
# O que ela NÃO faz, e isso é o conteúdo da aula: não cria training job, não troca
# endpoint, não promove modelo. Retreinar automaticamente com base em drift, sem
# ground truth e sem gate, é institucionalizar erro: se a distribuição mudou mas o
# modelo continua certo, o retraining automático joga fora um modelo bom; se o
# rótulo novo ainda não chegou, ele treina contra dados sem verdade conhecida.
#
# A reação segura é abrir incidente e segurar promoção.

resource "aws_lambda_function" "drift_response" {
  count = var.deploy_serving ? 1 : 0

  function_name = local.lambda_name
  description   = "Abre incidente de drift no S3 e publica ReactionTriggered. Não retreina nem troca modelo."
  role          = data.aws_iam_role.lab_role.arn
  handler       = "drift_response.lambda_handler"
  runtime       = var.lambda_runtime
  timeout       = var.lambda_timeout_seconds

  filename         = data.archive_file.drift_response.output_path
  source_code_hash = data.archive_file.drift_response.output_base64sha256

  environment {
    variables = {
      LAB_BUCKET        = terraform_data.bucket.output
      INCIDENTS_PREFIX  = local.s3_prefixes.incidents
      METRICS_NAMESPACE = var.metrics_namespace
      ENDPOINT_NAME     = local.endpoint_name
      EXPECTED_ALARM    = local.alarm_name
      MODEL_LINEAGE     = local.tags.model_lineage
    }
  }

  depends_on = [aws_cloudwatch_log_group.drift_response]
}

# Declarado explicitamente em vez de deixar a Lambda criar sozinha: um log group
# criado pelo runtime não pertence ao Terraform e sobrevive ao `make destroy`,
# ficando para trás com retenção infinita. Este é removido junto com o resto.
resource "aws_cloudwatch_log_group" "drift_response" {
  count = var.deploy_serving ? 1 : 0

  name              = "/aws/lambda/${local.lambda_name}"
  retention_in_days = var.log_retention_days
}

# Permissão de invocação como POLÍTICA DE RECURSO na própria Lambda, não como role
# nova: o Academy proíbe criar role de IAM. O EventBridge chega como principal
# events.amazonaws.com, e o source_arn amarra a permissão à regra deste lab —
# qualquer outra regra da conta continua sem poder invocar esta função.
resource "aws_lambda_permission" "allow_eventbridge" {
  count = var.deploy_serving ? 1 : 0

  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.drift_response[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.drift_alarm[0].arn
}
