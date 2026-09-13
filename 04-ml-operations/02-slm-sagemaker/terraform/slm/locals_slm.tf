# Arquivo transferido para A6a por decisão do coordenador (D21): o A6b ficou
# proibido de criar este arquivo, então não há risco de escrita concorrente.
#
# D21 pediu para levantar TODO local referenciado por sagemaker.tf/
# autoscaling.tf/dashboard.tf/outputs_slm.tf que não existisse em lugar
# nenhum do módulo. Conferido com
#   grep -ho 'local\.[a-zA-Z_][a-zA-Z0-9_]*' sagemaker.tf autoscaling.tf \
#     dashboard.tf outputs_slm.tf | sort -u
# contra os locals já declarados em cada arquivo: sagemaker.tf já declara seu
# próprio `locals { sagemaker_instance_type, llamacpp_image_uri,
# llamacpp_ctx_size, releases, s3_prefix_v1/v2, model_data_uri_v1/v2,
# model_name_v1/v2, endpoint_config_name_v1/v2 }`, e autoscaling.tf já declara
# `locals { autoscaling_min_capacity, autoscaling_max_capacity,
# autoscaling_target_invocations, autoscaling_scale_out_cooldown_s,
# autoscaling_scale_in_cooldown_s }` com o raciocínio de dimensionamento em
# comentário no próprio arquivo. Repeti-los aqui quebraria o plan por
# declaração duplicada no mesmo módulo — por isso este arquivo só contém os
# TRÊS locals que sobraram sem dono: dashboard_name (dashboard.tf referencia,
# nenhum arquivo declara) e endpoint_v1_name/endpoint_v2_name (sagemaker.tf e
# dashboard.tf referenciam; existiam em locals.tf do A6a até a decisão D20
# retirar nomenclatura específica de SageMaker de lá — ficaram órfãos até
# aqui).
locals {
  # Fórmula de config/lab.yaml (naming.dashboard: "fiap-mlops-slm-<suffix>")
  # — prefixo "fiap-mlops" de propósito, não "fiap-cbml-42": é o mesmo padrão
  # de nomenclatura de dashboard usado no Lab 04.1, para a turma reconhecer o
  # dashboard na lista do CloudWatch console independente do lab de origem.
  dashboard_name = "fiap-mlops-slm-${local.suffix}"

  # Fórmula de config/lab.yaml (naming.endpoint_v1/endpoint_v2) — mesmo
  # project_prefix/suffix da fundação (locals.tf, A6a), só o "slm-vN" no meio
  # é específico de SageMaker.
  endpoint_v1_name = "${local.project_prefix}-slm-v1-${local.suffix}"
  endpoint_v2_name = "${local.project_prefix}-slm-v2-${local.suffix}"
}
