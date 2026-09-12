# Dashboard do CloudWatch — a superfície que a aula lê durante o lab inteiro.
#
# Ele é provisionado por Terraform e não montado à mão no console por dois
# motivos: some junto com o resto no `make destroy` (o `verify-clean` confere), e o
# aluno recebe o mesmo layout que o professor projeta na tela.
#
# Regras de desenho aplicadas aqui, todas com o mesmo objetivo de legibilidade para
# público de BI:
#
# * cada widget tem no título a PERGUNTA que ele responde, não o nome da métrica;
# * no máximo 3 séries por visualização — 15 séries num gráfico não é informação,
#   é decoração;
# * latência convertida de microssegundo para milissegundo por metric math: a API
#   do SageMaker devolve microssegundo, e ninguém lê "1.240.000" como "1,2 s";
# * o limiar do alarme entra como linha anotada, para "cruzou" ser visível em vez
#   de calculado de cabeça;
# * baseline e drift aparecem como séries separadas onde a comparação é o conteúdo
#   (qualidade), e como evolução no tempo onde a virada é o conteúdo (PSI).
#
# Atualização: a granularidade mínima de métrica customizada é 60 s, e o console
# refaz a consulta em intervalo próprio. Isso é **tempo quase real** — o README
# nunca promete tempo real absoluto, porque não é o que o serviço entrega.
resource "aws_cloudwatch_dashboard" "mlops" {
  count = var.deploy_serving ? 1 : 0

  dashboard_name = local.dashboard_name

  dashboard_body = jsonencode({
    start          = "-PT1H"
    periodOverride = "inherit"

    widgets = [
      # ------------------------------------------------------------------- #
      { # Cabeçalho: dá a ordem de leitura antes de qualquer número.
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2
        properties = {
          markdown = join("\n", [
            "## Bora Fibra · churn-v1 em operação",
            "Leia de cima para baixo: **infraestrutura** (está no ar?) → **dados** (o mundo mudou?) → **predições** (o modelo mudou de opinião?) → **qualidade** (isso virou erro?) → **reação** (alguém foi avisado?).",
            "As quatro primeiras linhas podem contar histórias diferentes ao mesmo tempo. É exatamente esse o ponto do laboratório.",
          ])
        }
      },

      # ---------------------- linha 1: infraestrutura --------------------- #
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 6
        height = 6
        properties = {
          title     = "O endpoint está atendendo? (chamadas)"
          view      = "singleValue"
          region    = var.region
          stat      = "Sum"
          period    = 300
          sparkline = true
          metrics = [
            ["AWS/SageMaker", "Invocations", "EndpointName", local.endpoint_name, "VariantName", "AllTraffic", { label = "Invocações" }],
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
          title     = "Deu erro de HTTP? (4XX / 5XX)"
          view      = "singleValue"
          region    = var.region
          stat      = "Sum"
          period    = 300
          sparkline = true
          metrics = [
            ["AWS/SageMaker", "Invocation4XXErrors", "EndpointName", local.endpoint_name, "VariantName", "AllTraffic", { label = "Erros 4XX" }],
            ["AWS/SageMaker", "Invocation5XXErrors", "EndpointName", local.endpoint_name, "VariantName", "AllTraffic", { label = "Erros 5XX" }],
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
          title  = "Está lento? (latência em milissegundos)"
          view   = "timeSeries"
          region = var.region
          stat   = "Average"
          period = 60
          yAxis  = { left = { label = "ms", showUnits = false } }
          metrics = [
            [{ expression = "m1/1000", label = "Latência do modelo (ms)", id = "e1" }],
            [{ expression = "m2/1000", label = "Overhead da plataforma (ms)", id = "e2" }],
            ["AWS/SageMaker", "ModelLatency", "EndpointName", local.endpoint_name, "VariantName", "AllTraffic", { id = "m1", visible = false }],
            ["AWS/SageMaker", "OverheadLatency", "EndpointName", local.endpoint_name, "VariantName", "AllTraffic", { id = "m2", visible = false }],
          ]
        }
      },

      # -------------------------- linha 2: dados -------------------------- #
      {
        type   = "metric"
        x      = 0
        y      = 8
        width  = 12
        height = 6
        properties = {
          title  = "Os dados ainda parecem os mesmos? (PSI máximo)"
          view   = "timeSeries"
          region = var.region
          stat   = "Maximum"
          period = 60
          yAxis  = { left = { label = "PSI", showUnits = false, min = 0 } }
          annotations = {
            horizontal = [{
              label = "limiar do alarme (${var.drift_alarm_threshold})"
              value = var.drift_alarm_threshold
              color = "#d13212"
              fill  = "above"
            }]
          }
          metrics = [
            [var.metrics_namespace, "DataDriftPSIMax", "EndpointName", local.endpoint_name, "Window", "baseline", { label = "Janela baseline" }],
            [var.metrics_namespace, "DataDriftPSIMax", "EndpointName", local.endpoint_name, "Window", "drift", { label = "Janela com drift" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 8
        width  = 12
        height = 6
        properties = {
          title  = "Qual variável mudou? (PSI por feature)"
          view   = "timeSeries"
          region = var.region
          stat   = "Maximum"
          period = 60
          yAxis  = { left = { label = "PSI", showUnits = false, min = 0 } }
          annotations = {
            horizontal = [{
              label = "limiar (${var.drift_alarm_threshold})"
              value = var.drift_alarm_threshold
              color = "#d13212"
            }]
          }
          metrics = [
            for feature in ["monthly_charges", "support_calls_90d", "usage_score"] :
            [var.metrics_namespace, "DataDriftPSI_${feature}", "EndpointName", local.endpoint_name, { label = feature }]
          ]
        }
      },

      # ------------------------ linha 3: predições ------------------------ #
      {
        type   = "metric"
        x      = 0
        y      = 14
        width  = 12
        height = 6
        properties = {
          title  = "O modelo mudou de opinião? (PSI das predições)"
          view   = "timeSeries"
          region = var.region
          stat   = "Maximum"
          period = 60
          yAxis  = { left = { label = "PSI", showUnits = false, min = 0 } }
          annotations = {
            horizontal = [{
              label = "faixa de atenção (${var.drift_alarm_threshold})"
              value = var.drift_alarm_threshold
              color = "#ff7f0e"
            }]
          }
          metrics = [
            [var.metrics_namespace, "PredictionDriftPSI", "EndpointName", local.endpoint_name, { label = "PSI do score" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 14
        width  = 12
        height = 6
        properties = {
          title  = "Está prevendo mais churn? (taxa prevista e probabilidade média)"
          view   = "singleValue"
          region = var.region
          stat   = "Average"
          period = 300
          metrics = [
            [var.metrics_namespace, "PredictedChurnRate", "EndpointName", local.endpoint_name, "Window", "baseline", { label = "Taxa prevista · baseline" }],
            [var.metrics_namespace, "PredictedChurnRate", "EndpointName", local.endpoint_name, "Window", "drift", { label = "Taxa prevista · drift" }],
            [var.metrics_namespace, "MeanChurnProbability", "EndpointName", local.endpoint_name, "Window", "baseline", { label = "Prob. média · baseline" }],
            [var.metrics_namespace, "MeanChurnProbability", "EndpointName", local.endpoint_name, "Window", "drift", { label = "Prob. média · drift" }],
          ]
        }
      },

      # ------------------------ linha 4: qualidade ------------------------ #
      {
        type   = "metric"
        x      = 0
        y      = 20
        width  = 12
        height = 6
        properties = {
          title  = "A qualidade aguentou? (F1, quando o ground truth chega)"
          view   = "singleValue"
          region = var.region
          stat   = "Average"
          period = 300
          metrics = [
            [var.metrics_namespace, "ModelQualityF1", "EndpointName", local.endpoint_name, "Window", "baseline", { label = "F1 · baseline" }],
            [var.metrics_namespace, "ModelQualityF1", "EndpointName", local.endpoint_name, "Window", "drift", { label = "F1 · drift" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 20
        width  = 12
        height = 6
        properties = {
          title  = "E a ordenação? (ROC-AUC, quando o ground truth chega)"
          view   = "singleValue"
          region = var.region
          stat   = "Average"
          period = 300
          metrics = [
            [var.metrics_namespace, "ModelQualityROCAUC", "EndpointName", local.endpoint_name, "Window", "baseline", { label = "ROC-AUC · baseline" }],
            [var.metrics_namespace, "ModelQualityROCAUC", "EndpointName", local.endpoint_name, "Window", "drift", { label = "ROC-AUC · drift" }],
          ]
        }
      },

      # -------------------------- linha 5: reação ------------------------- #
      {
        type   = "alarm"
        x      = 0
        y      = 26
        width  = 12
        height = 6
        properties = {
          title  = "A regra virou incidente? (estado do alarme)"
          alarms = [aws_cloudwatch_metric_alarm.data_drift[0].arn]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 26
        width  = 12
        height = 6
        properties = {
          title     = "O sistema reagiu sozinho? (ReactionTriggered)"
          view      = "singleValue"
          region    = var.region
          stat      = "Sum"
          period    = 300
          sparkline = true
          metrics = [
            [var.metrics_namespace, "ReactionTriggered", "EndpointName", local.endpoint_name, { label = "Reações disparadas" }],
          ]
        }
      },
    ]
  })
}
