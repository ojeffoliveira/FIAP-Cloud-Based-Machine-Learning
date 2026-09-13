locals {
  account_id = data.aws_caller_identity.current.account_id

  # Sufixo curto e determinístico — nunca aleatório. O mesmo account_id e o
  # mesmo owner do GitHub sempre produzem o mesmo sufixo, então destruir e
  # recriar o runner não troca nome nenhum (idempotência exigida na spec,
  # 09_IMPLEMENTATION_BLUEPRINT.md §17). Diferente do stack de treino do 04.1,
  # aqui não existe risco de "nome já usado no histórico" — EC2/SG toleram
  # recriação com o mesmo nome depois de um destroy.
  suffix = substr(sha256("${local.account_id}-${var.github_repository_owner}"), 0, 8)

  name_prefix   = "${var.project_prefix}-runner"
  instance_name = "${local.name_prefix}-${local.suffix}"
  sg_name       = "${local.name_prefix}-sg-${local.suffix}"

  github_repo_url       = "https://github.com/${var.github_repository_owner}/${var.github_repository_name}"
  github_new_runner_url = "${local.github_repo_url}/settings/actions/runners/new"
  github_runners_url    = "${local.github_repo_url}/settings/actions/runners"

  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    terraform_version            = var.terraform_version
    terraform_linux_amd64_sha256 = var.terraform_linux_amd64_sha256
    runner_version               = var.github_actions_runner_version
    runner_linux_x64_sha256      = var.github_actions_runner_linux_x64_sha256
    jq_version                   = var.jq_version
    jq_linux_amd64_sha256        = var.jq_linux_amd64_sha256
    aws_cli_gpg_public_key       = var.aws_cli_gpg_public_key
    # Impressão digital pública da chave GPG oficial da AWS (não é segredo):
    # o user-data usa esse valor só para confirmar, antes de importar a chave
    # baixada por HTTPS, que ela é exatamente a publicada pela AWS em
    # https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html#getting-started-install-verify —
    # é a verificação de assinatura do instalador da AWS CLI v2 que a decisão
    # D8 exige (egress 443-only, sem apt-get). gitleaks:allow
    aws_cli_gpg_key_fingerprint = "FB5DB77FD5C118B80511ADA8A6310ACC4672475C" # gitleaks:allow
  })

  # Nenhum dado pessoal nas tags: elas caem em relatório de custo que a turma
  # toda vê. Mesmo padrão de chaves do Lab 04.1.
  tags = {
    course     = "cloud-based-machine-learning"
    lab        = "lab04.2-slm-sagemaker"
    component  = "github-actions-self-hosted-runner"
    purpose    = "education"
    managed_by = "terraform"
  }
}
