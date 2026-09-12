data "aws_caller_identity" "current" {}

data "aws_iam_role" "lab_role" {
  name = var.execution_role_name
}

# O pacote da Lambda é montado no apply, a partir do fonte versionado em lambda/.
# O .zip em si nunca entra no repositório: ele é derivado, e `build/` está no
# .gitignore.
data "archive_file" "drift_response" {
  type        = "zip"
  source_file = "${path.module}/../lambda/drift_response.py"
  output_path = local.lambda_package_path
}
