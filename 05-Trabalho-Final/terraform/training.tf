# Training Job do SageMaker: criado por boto3 (src/final_project/training.py),
# não por um resource deste arquivo — decisão congelada em CONTRATO.md.
#
# Os labs anteriores (02-ml-system, 03-serving-and-scaling,
# 04-ml-operations/01-observability-drift-response) declaram
# `aws_sagemaker_training_job` porque cada um roda um único ciclo de
# `terraform apply` por execução. O trabalho final não tem esse luxo: a
# Regra 3 do contrato exige que troca de `student/solution.yaml` + `make run`
# nunca dispare `terraform apply` nem retreino, e o CLI (`final.py train` /
# `training-status` / `artifact`) precisa poder ser chamado várias vezes,
# fora do ciclo de vida do Terraform. Amarrar o treino a um `resource` faria
# o próximo `terraform apply` decidir quando treinar de novo — o oposto do
# que a Regra 3 pede. Por isso o job nasce, é monitorado e tem o artefato
# confirmado inteiramente em Python/boto3 (upload de train/validation para o
# S3 incluído: nenhum `aws_s3_object` aqui, pelo mesmo motivo — o CLI precisa
# subir o dado mais recente de `artifacts/data/` a cada `train`, sem depender
# de um `apply` anterior ter sincronizado o arquivo certo).
#
# O que sobra para o Terraform, e é o que este arquivo faz: garantir que o
# estágio 2 nunca aplica sem o handoff do treino ter acontecido de verdade.
# `var.model_artifact_s3_uri` nunca é montado por convenção de caminho —
# `training.py` só escreve essa variável em
# `.generated/artifact.auto.tfvars.json` depois de ler
# `DescribeTrainingJob.ModelArtifacts.S3ModelArtifacts` e confirmar o objeto
# com `HeadObject`. Este `check` transforma "esqueci de rodar `train` e
# `artifact` antes do estágio 2" num erro de `plan`, não numa falha silenciosa
# dentro de `model.tf` (A5).
check "model_artifact_ready" {
  assert {
    condition     = !var.enable_serving || var.model_artifact_s3_uri != ""
    error_message = <<-EOT
      var.enable_serving = true mas var.model_artifact_s3_uri está vazio.
      O estágio de serving depende do handoff real do treino: rode
      `final.py train` seguido de `final.py artifact` (ou os targets
      equivalentes do Makefile) antes de aplicar o estágio 2. Os dois juntos
      escrevem .generated/artifact.auto.tfvars.json com a URI que a API do
      SageMaker confirmou via DescribeTrainingJob + HeadObject — nunca um
      caminho montado por convenção.
    EOT
  }
}
