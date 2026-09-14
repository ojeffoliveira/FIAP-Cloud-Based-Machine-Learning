# Único arquivo de outputs do trabalho final: A0 escreve todos de uma vez
# para A5 e A6 terem um contrato estável desde a onda 1. A5 e A6 consomem,
# nunca editam este arquivo — pedido de output adicional vai para
# /tmp/tf-final/shared/PEDIDOS.md.
#
# Os recursos abaixo (aws_sagemaker_model.churn, aws_sagemaker_endpoint.*,
# aws_lambda_function.drift_response, aws_cloudwatch_dashboard.final) são
# criados por OUTROS agentes com `count = var.enable_serving ? 1 : 0`:
#   - model.tf, realtime.tf, serverless.tf, async.tf -> A5
#   - lambda.tf, dashboard.tf                          -> A6
#
# Duas consequências, uma por estágio do trabalho do repositório:
#   1. Enquanto esses arquivos ainda não existem (fim da onda 1 de A0),
#      `terraform validate` falha aqui com "reference to undeclared
#      resource". Esperado — deixa de acontecer quando A5/A6 entregam.
#   2. Depois que existirem, com var.enable_serving = false (estágio 1),
#      count = 0 torna cada resource uma lista vazia. `one(...)` devolve null
#      nesse caso, mas `coalesce(null, "")` NÃO devolve "": o Terraform erra
#      a chamada quando TODOS os argumentos são null ou string vazia, e o ""
#      literal do fallback não se salva a si mesmo — então esse output falha
#      garantidamente em todo estágio 1, não é caso de borda. Corrigido para
#      `join("", recurso[*].campo)`: lista vazia gera "" sem erro, lista de
#      1 elemento gera o valor — sobrevive ao estágio 1 e devolve o nome real
#      no estágio 2 (enable_serving = true), quando o recurso existe de fato.

output "bucket_name" {
  description = "Bucket único do trabalho final (dados, artefatos, async, incidentes). Existe desde o estágio 1."
  value       = local.bucket_name
}

output "model_name" {
  description = "Nome do SageMaker Model, ou vazio antes do estágio de serving. Criado por A5 em model.tf."
  value       = join("", aws_sagemaker_model.churn[*].name)
}

output "realtime_endpoint_name" {
  description = "Endpoint Real-Time, ou vazio antes do estágio de serving. Criado por A5 em realtime.tf."
  value       = join("", aws_sagemaker_endpoint.realtime[*].name)
}

output "serverless_endpoint_name" {
  description = "Endpoint Serverless, ou vazio antes do estágio de serving. Criado por A5 em serverless.tf."
  value       = join("", aws_sagemaker_endpoint.serverless[*].name)
}

output "async_endpoint_name" {
  description = "Endpoint Async, ou vazio antes do estágio de serving. Criado por A5 em async.tf."
  value       = join("", aws_sagemaker_endpoint.async[*].name)
}

output "async_output_s3_uri" {
  description = "Prefixo no S3 em que o Async Inference escreve a saída. Existe desde o estágio 1: é só um prefixo do bucket, não depende de nenhum endpoint estar de pé."
  value       = local.async_output_uri
}

output "lambda_function_name" {
  description = "Lambda de reação a drift, ou vazio antes do estágio de serving. Criada por A6 em lambda.tf."
  value       = join("", aws_lambda_function.drift_response[*].function_name)
}

output "dashboard_name" {
  description = "Dashboard do CloudWatch, ou vazio antes do estágio de serving. Criado por A6 em dashboard.tf."
  value       = join("", aws_cloudwatch_dashboard.final[*].dashboard_name)
}

output "dashboard_url" {
  description = "Link direto para o dashboard no console, ou vazio antes do estágio de serving. O aluno não deve caçar o nome à mão."
  value       = join("", aws_cloudwatch_dashboard.final[*].dashboard_name) != "" ? local.dashboard_url : ""
}

output "region" {
  description = "Região fixa de todos os recursos do trabalho final."
  value       = var.aws_region
}

output "prefix" {
  description = "Prefixo comum de nome de todos os recursos do trabalho final."
  value       = local.prefix
}
