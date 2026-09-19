# Dashboard CloudWatch do SLM — peça didática central para a turma de BI ler o
# comportamento de V1 x V2 durante a aula. Público: analista de BI, não
# plantonista de SRE — título do widget é a PERGUNTA que ele responde, nunca o
# nome da métrica (spec do lab 03, replicada aqui).
#
# REGRA MAIS IMPORTANTE DESTA VERSÃO: comparação mora no MESMO widget. A versão
# anterior tinha um widget para "V1 está recebendo chamadas?" e outro para "V2
# está recebendo chamadas?" (e o mesmo par para erro) — isso obrigava o aluno a
# olhar dois números em dois cantos da tela e fazer a subtração de cabeça, que é
# exatamente o trabalho que o painel deveria poupar. Agora tráfego e erro têm
# UM widget cada, com as duas séries (V1 e V2) juntas. Latência e overhead já
# eram assim desde a versão anterior (mantidos).
#
# Criado junto com V1 (não depende de enable_v2): os widgets com série de V2
# simplesmente não têm dado até o release V2 existir — CloudWatch não erra
# métrica ausente, só mostra o gráfico vazio. Isso é intencional: o dashboard
# já nasce com a forma final, a aula vê o "antes" (V1 só, Parte 3) e o "depois"
# (V1+V2, Parte 7 em diante) no mesmo painel, sem precisar de um segundo apply.
#
# Alarmes: nenhum criado aqui de propósito. Calibrar um alarme de latência ou
# erro sem uma janela real de tráfego observado (o lab é uma demo de poucos
# minutos, não um serviço com histórico) arriscaria falso positivo. O único
# limiar de negócio que existe de fato é o alvo de autoscaling — esse entra
# como anotação horizontal no próprio widget de autoscaling, não como alarme.
#
# Nome do painel carrega `${local.suffix}` (locals_slm.tf) — dois ciclos na
# mesma conta nunca sobrescrevem o painel um do outro, e `verify-clean` acha
# por prefixo, então o sufixo não atrapalha a limpeza.
resource "aws_cloudwatch_dashboard" "slm" {
  count = var.enable_v1 ? 1 : 0

  dashboard_name = local.dashboard_name

  dashboard_body = jsonencode({
    start          = "-PT3H"
    periodOverride = "inherit"

    widgets = [
      # ----------------------------- cabeçalho ---------------------------- #
      # Ordem de leitura amarrada às Partes do README: cada linha do painel é
      # onde o TRÁFEGO daquela linha nasce, não necessariamente onde o aluno
      # está olhando o painel (isso só acontece de fato na Parte 8, passo 30).
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 3
        properties = {
          markdown = join("\n", [
            "## Bora Fibra · copiloto de retenção (SLM) — V1 x V2",
            "**Pergunta que o painel responde:** V2 (pipeline, autoscaling) entrega tão bem quanto V1 (manual, capacidade fixa) — e a que custo de erro ou fila?",
            "**Linha 1 (tráfego e latência)** — dado de V1 nasce na Parte 3, passo 14 (`make benchmark-v1`); dado de V2 nasce dentro do próprio pipeline de deploy, Parte 7, passo 26. **Linha 2 (overhead e erros)** — mesmas Partes. **Linha 3 (release ativa e autoscaling)** — o alvo de scale-out de V2 é confirmado por API na Parte 8, passo 28; a leitura do painel completo, lado a lado, é o passo 30.",
            "Toda série de V2 aparece vazia até o passo 26 fechar verde — comportamento correto, não bug: a métrica só existe depois da primeira chamada ao endpoint V2.",
          ])
        }
      },

      # ------------------------- linha 1: tráfego e latência ------------------------- #
      {
        type   = "metric"
        x      = 0
        y      = 3
        width  = 6
        height = 6
        properties = {
          # Um widget só, duas séries: é a mesma pergunta ("está recebendo
          # chamada?") feita das duas releases, e a resposta que importa é a
          # comparação, não o valor isolado de cada uma.
          title     = "V1 e V2 estão recebendo chamadas?"
          view      = "singleValue"
          region    = var.region
          stat      = "Sum"
          period    = 300
          sparkline = true
          metrics = [
            ["AWS/SageMaker", "Invocations", "EndpointName", local.endpoint_v1_name, "VariantName", local.releases.v1.variant_name, { label = "Invocações V1" }],
            ["AWS/SageMaker", "Invocations", "EndpointName", local.endpoint_v2_name, "VariantName", local.releases.v2.variant_name, { label = "Invocações V2" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 6
        y      = 3
        width  = 18
        height = 6
        properties = {
          # ModelLatency vem em MICROssegundo (API do SageMaker, confirmado no
          # lab 03) — ninguém lê "1.240.000" como "1,24 s". m1/m2 ficam ocultos
          # (visible = false) só para alimentar a expressão; o que o BI vê são
          # e1/e2, já em ms.
          title  = "Quem responde mais rápido — V1 ou V2? (latência do modelo, ms)"
          view   = "timeSeries"
          region = var.region
          stat   = "Average"
          period = 60
          yAxis  = { left = { label = "ms", showUnits = false, min = 0 } }
          metrics = [
            [{ expression = "m1/1000", label = "ModelLatency V1 (ms)", id = "e1" }],
            [{ expression = "m2/1000", label = "ModelLatency V2 (ms)", id = "e2" }],
            ["AWS/SageMaker", "ModelLatency", "EndpointName", local.endpoint_v1_name, "VariantName", local.releases.v1.variant_name, { id = "m1", visible = false }],
            ["AWS/SageMaker", "ModelLatency", "EndpointName", local.endpoint_v2_name, "VariantName", local.releases.v2.variant_name, { id = "m2", visible = false }],
          ]
        }
      },

      # ------------------------- linha 2: overhead e erros ------------------------- #
      {
        type   = "metric"
        x      = 0
        y      = 9
        width  = 12
        height = 6
        properties = {
          # OverheadLatency também vem em microssegundo. É o tempo que o
          # SageMaker gasta FORA do modelo (fila, roteamento) — não existe
          # `ModelSetupTime` para este container (llama.cpp DLC), então esta é
          # a métrica honesta para "quanto é plataforma, não modelo".
          title  = "Quanto é fila/plataforma, não modelo? (overhead, ms)"
          view   = "timeSeries"
          region = var.region
          stat   = "Average"
          period = 60
          yAxis  = { left = { label = "ms", showUnits = false, min = 0 } }
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
        y      = 9
        width  = 12
        height = 6
        properties = {
          # 4XX e 5XX somados por metric math (m1+m2 para V1, m3+m4 para V2):
          # a pergunta de BI é "deu erro?", não "foi erro de cliente ou de
          # servidor?" — separar as quatro métricas cruas neste widget
          # estouraria o limite de 3 séries visíveis por decoração, sem
          # ganhar nada de leitura. As quatro métricas cruas ficam ocultas
          # (visible = false) só para alimentar as duas expressões exibidas.
          title     = "V1 ou V2 deu erro? (4XX + 5XX)"
          view      = "singleValue"
          region    = var.region
          stat      = "Sum"
          period    = 300
          sparkline = true
          metrics = [
            [{ expression = "m1+m2", label = "Erros V1 (4XX+5XX)", id = "e1" }],
            [{ expression = "m3+m4", label = "Erros V2 (4XX+5XX)", id = "e2" }],
            ["AWS/SageMaker", "Invocation4XXErrors", "EndpointName", local.endpoint_v1_name, "VariantName", local.releases.v1.variant_name, { id = "m1", visible = false }],
            ["AWS/SageMaker", "Invocation5XXErrors", "EndpointName", local.endpoint_v1_name, "VariantName", local.releases.v1.variant_name, { id = "m2", visible = false }],
            ["AWS/SageMaker", "Invocation4XXErrors", "EndpointName", local.endpoint_v2_name, "VariantName", local.releases.v2.variant_name, { id = "m3", visible = false }],
            ["AWS/SageMaker", "Invocation5XXErrors", "EndpointName", local.endpoint_v2_name, "VariantName", local.releases.v2.variant_name, { id = "m4", visible = false }],
          ]
        }
      },

      # --------------------- linha 3: release e autoscaling ----------------- #
      {
        type   = "text"
        x      = 0
        y      = 15
        width  = 12
        height = 6
        properties = {
          markdown = join("\n", [
            "### Qual release está ativa?",
            "**V1** (`${local.releases.v1.filename}`, quantização Q4_0) — sempre ativa, capacidade fixa em 1 instância `${local.sagemaker_instance_type}`. É o caminho manual, provado no Codespaces (Parte 2).",
            "**V2** (`${local.releases.v2.filename}`, quantização Q4_K_M) — publicada pelo pipeline (runner self-hosted efêmero, Parte 7), com autoscaling de 1 a 2 instâncias.",
            "Qual das duas é a **recomendada** para uso é decisão de `make compare` (Parte 8, passo 29), cruzando latência e qualidade — este painel mostra o estado, não a recomendação.",
          ])
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 15
        width  = 12
        height = 6
        properties = {
          # Widget V2-only de propósito: não há comparação V1 x V2 aqui porque
          # V1 não escala (capacidade fixa, decisão da missão) — não existe
          # segunda série para comparar sem inventar uma.
          #
          # O que o SageMaker NÃO entrega: contagem de instâncias direta. O
          # alvo de autoscaling é sobre INVOCAÇÕES POR INSTÂNCIA
          # (`InvocationsPerInstance`, métrica que a Application Auto Scaling
          # de fato observa — `predefined_metric_type =
          # SageMakerVariantInvocationsPerInstance` em autoscaling.tf), não
          # sobre quantidade de máquinas. A anotação horizontal usa
          # `local.autoscaling_target_invocations` (autoscaling.tf) para o
          # "cruzou o alvo" ser visível em vez de calculado de cabeça.
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
