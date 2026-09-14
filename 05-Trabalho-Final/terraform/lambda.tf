# Lambda de reação — o que o sistema faz sozinho quando um dos dois alarmes
# de drift dispara.
#
# O que ela NÃO faz, e isso é o conteúdo da aula (ver lambda/drift_response.py
# para a versão completa do argumento): não cria training job, não troca
# endpoint, não promove modelo. Abre incidente e segura promoção — quem decide
# depois de olhar a evidência é o aluno, em student/DECISION.md.

# Empacotado no próprio apply a partir do fonte versionado em
# lambda/drift_response.py, saída em local.lambda_package_path — nunca
# comitado como .zip. O provider archive já está pinado em versions.tf (A0);
# este arquivo só consome `data.archive_file`, como o comentário de lá pede.
data "archive_file" "drift_response" {
  type        = "zip"
  source_file = "${path.module}/../lambda/drift_response.py"
  output_path = local.lambda_package_path
}

resource "aws_lambda_function" "drift_response" {
  count = var.enable_serving ? 1 : 0

  function_name = local.lambda_name
  description   = "Abre incidente de drift no S3 e publica ReactionTriggered. Não retreina, não troca modelo, não decide go-live nem rollback."
  role          = data.aws_iam_role.lab.arn
  handler       = "drift_response.lambda_handler"
  runtime       = var.lambda_runtime
  timeout       = var.lambda_timeout_seconds

  filename         = data.archive_file.drift_response.output_path
  source_code_hash = data.archive_file.drift_response.output_base64sha256

  environment {
    variables = {
      LAB_BUCKET                  = terraform_data.bucket.output
      INCIDENTS_PREFIX            = local.s3_prefixes.incidents
      METRICS_NAMESPACE           = var.metrics_namespace
      PSI_THRESHOLD               = tostring(var.psi_threshold)
      DATA_DRIFT_ALARM_NAME       = local.data_drift_alarm_name
      PREDICTION_DRIFT_ALARM_NAME = local.prediction_drift_alarm_name
      MODEL_LINEAGE               = local.tags.model_lineage
    }
  }

  depends_on = [aws_cloudwatch_log_group.drift_response]
}

# Declarado explicitamente em vez de deixar a Lambda criar sozinha: um log
# group criado pelo runtime não pertence ao Terraform e sobrevive ao
# `make finish`/destroy, ficando para trás com retenção infinita.
resource "aws_cloudwatch_log_group" "drift_response" {
  count = var.enable_serving ? 1 : 0

  name              = "/aws/lambda/${local.lambda_name}"
  retention_in_days = var.log_retention_days
}

# Permissão de invocação como POLÍTICA DE RECURSO na própria Lambda, não como
# role nova: o Academy proíbe criar role de IAM (ACADEMY.md item 2). O
# EventBridge chega como principal events.amazonaws.com, e o source_arn amarra
# a permissão à regra deste trabalho final — nenhuma outra regra da conta pode
# invocar esta função.
resource "aws_lambda_permission" "allow_eventbridge" {
  count = var.enable_serving ? 1 : 0

  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.drift_response[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.drift_alarm[0].arn
}
