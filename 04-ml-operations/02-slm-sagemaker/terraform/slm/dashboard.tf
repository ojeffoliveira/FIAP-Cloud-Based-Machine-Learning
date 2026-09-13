# Dashboard CloudWatch do SLM — peça didática central para a turma de BI ler o
# comportamento de V1 x V2 durante a aula, no mesmo espírito do dashboard do
# 04.1 (terraform/dashboard.tf daquele lab): título = pergunta que o widget
# responde, no máximo 3 séries por gráfico, latência convertida de
# microssegundo para milissegundo por metric math.
#
# Criado junto com V1 (não depende de enable_v2): os widgets de V2 simplesmente
# não têm dado até o release V2 existir — CloudWatch não erra métrica ausente,
# só mostra o gráfico vazio. Isso é intencional: o dashboard já nasce com a
# forma final, a aula vê o "antes" (V1 só) e o "depois" (V1+V2) no mesmo painel.
#
# Alarmes: nenhum criado aqui de propósito. Calibrar um alarme de latência ou
# erro sem uma janela real de tráfego observado (o lab é uma demo de poucos
# minutos, não um serviço com histórico) arriscaria falso positivo — e a
# missão proíbe alarme sem calibração segura. O widget de alarme do 04.1
# funciona porque há um limiar de negócio (PSI) definido a priori; aqui não há
# equivalente sem inventar um número.
resource "aws_cloudwatch_dashboard" "slm" {
  count = var.enable_v1 ? 1 : 0

  dashboard_name = local.dashboard_name

  dashboard_body = jsonencode({
    start          = "-PT3H"
    periodOverride = "inherit"

    widgets = [
      # ----------------------------- cabeçalho ---------------------------- #
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2
        properties = {
          markdown = join("\n", [
            "## Bora Fibra · copiloto de retenção (SLM) — V1 x V2",
            "V1 (Q4_0) é o release manual, capacidade fixa. V2 (Q4_K_M) chega pelo pipeline, com autoscaling. Leia: **tráfego** → **latência** → **erros** → **release ativa** → **autoscaling**.",
          ])
        }
      },

      # ------------------------- linha 1: tráfego -------------------------- #
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 6
        height = 6
        properties = {
          title     = "V1 está recebendo chamadas?"
          view      = "singleValue"
          region    = var.region
          stat      = "Sum"
          period    = 300
          sparkline = true
          metrics = [
            ["AWS/SageMaker", "Invocations", "EndpointName", local.endpoint_v1_name, "VariantName", local.releases.v1.variant_name, { label = "Invocações V1" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 6
        y      = 2
        width  = 6
        height = 6
        properties = {
          title     = "V2 está recebendo chamadas?"
          view      = "singleValue"
          region    = var.region
          stat      = "Sum"
          period    = 300
          sparkline = true
          metrics = [
            ["AWS/SageMaker", "Invocations", "EndpointName", local.endpoint_v2_name, "VariantName", local.releases.v2.variant_name, { label = "Invocações V2" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 2
        width  = 12
        height = 6
        properties = {
          title  = "Quem responde mais rápido — V1 ou V2? (latência do modelo, ms)"
          view   = "timeSeries"
          region = var.region
          stat   = "Average"
          period = 60
          yAxis  = { left = { label = "ms", showUnits = false } }
          metrics = [
            [{ expression = "m1/1000", label = "ModelLatency V1 (ms)", id = "e1" }],
            [{ expression = "m2/1000", label = "ModelLatency V2 (ms)", id = "e2" }],
            ["AWS/SageMaker", "ModelLatency", "EndpointName", local.endpoint_v1_name, "VariantName", local.releases.v1.variant_name, { id = "m1", visible = false }],
            ["AWS/SageMaker", "ModelLatency", "EndpointName", local.endpoint_v2_name, "VariantName", local.releases.v2.variant_name, { id = "m2", visible = false }],
          ]
        }
      },

      # ------------------------- linha 2: overhead ------------------------- #
      {
        type   = "metric"
        x      = 0
        y      = 8
        width  = 12
        height = 6
        properties = {
          title  = "Quanto é fila/plataforma, não modelo? (overhead, ms)"
          view   = "timeSeries"
          region = var.region
          stat   = "Average"
          period = 60
          yAxis  = { left = { label = "ms", showUnits = false } }
          metrics = [
            [{ expression = "m1/1000", label = "OverheadLatency V1 (ms)", id = "e1" }],
            [{ expression = "m2/1000", label = "OverheadLatency V2 (ms)", id = "e2" }],
            ["AWS/SageMaker", "OverheadLatency", "EndpointName", local.endpoint_v1_name, "VariantName", local.releases.v1.variant_name, { id = "m1", visible = false }],
            ["AWS/SageMaker", "OverheadLatency", "EndpointName", local.endpoint_v2_name, "VariantName", local.releases.v2.variant_name, { id = "m2", visible = false }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 8
        width  = 6
        height = 6
        properties = {
          title     = "V1 deu erro? (4XX / 5XX)"
          view      = "singleValue"
          region    = var.region
          stat      = "Sum"
          period    = 300
          sparkline = true
          metrics = [
            ["AWS/SageMaker", "Invocation4XXErrors", "EndpointName", local.endpoint_v1_name, "VariantName", local.releases.v1.variant_name, { label = "4XX V1" }],
            ["AWS/SageMaker", "Invocation5XXErrors", "EndpointName", local.endpoint_v1_name, "VariantName", local.releases.v1.variant_name, { label = "5XX V1" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 18
        y      = 8
        width  = 6
        height = 6
        properties = {
          title     = "V2 deu erro? (4XX / 5XX)"
          view      = "singleValue"
          region    = var.region
          stat      = "Sum"
          period    = 300
          sparkline = true
          metrics = [
            ["AWS/SageMaker", "Invocation4XXErrors", "EndpointName", local.endpoint_v2_name, "VariantName", local.releases.v2.variant_name, { label = "4XX V2" }],
            ["AWS/SageMaker", "Invocation5XXErrors", "EndpointName", local.endpoint_v2_name, "VariantName", local.releases.v2.variant_name, { label = "5XX V2" }],
          ]
        }
      },

      # --------------------- linha 3: release e autoscaling ----------------- #
      {
        type   = "text"
        x      = 0
        y      = 14
        width  = 12
        height = 6
        properties = {
          markdown = join("\n", [
            "### Qual release está ativa?",
            "**V1** (`${local.releases.v1.filename}`, quantização Q4_0) — sempre ativa, capacidade fixa em 1 instância `${local.sagemaker_instance_type}`. É o caminho manual, provado no Codespaces.",
            "**V2** (`${local.releases.v2.filename}`, quantização Q4_K_M) — publicada pelo pipeline (runner self-hosted efêmero), com autoscaling de 1 a 2 instâncias.",
            "Qual das duas é a **recomendada** para uso é decisão de `make compare` (A8), cruzando latência e qualidade — este painel mostra o estado, não a recomendação.",
          ])
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 14
        width  = 12
        height = 6
        properties = {
          title  = "V2 está perto de escalar? (invocações/instância x alvo)"
          view   = "timeSeries"
          region = var.region
          stat   = "Average"
          period = 60
          yAxis  = { left = { label = "invocações/instância/min", showUnits = false, min = 0 } }
          annotations = {
            horizontal = [{
              label = "alvo de scale-out (${local.autoscaling_target_invocations})"
              value = local.autoscaling_target_invocations
              color = "#d13212"
              fill  = "above"
            }]
          }
          metrics = [
            ["AWS/SageMaker", "InvocationsPerInstance", "EndpointName", local.endpoint_v2_name, "VariantName", local.releases.v2.variant_name, { label = "Invocações/instância V2" }],
          ]
        }
      },
    ]
  })
}
