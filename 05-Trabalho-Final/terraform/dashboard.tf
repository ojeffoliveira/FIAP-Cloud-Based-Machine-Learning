# Dashboard do CloudWatch — a superfície que a aula lê durante o lab inteiro.
#
# Provisionado por Terraform (não montado à mão no console) para dois
# motivos: some junto com o resto no `make finish`, e o aluno recebe o mesmo
# layout que seria usado em qualquer correção.
#
# A ordem de leitura conta a história pedagógica de cima para baixo:
# baseline saudável -> PSI cruzando o limiar -> a predição mudando de opinião
# -> a qualidade real, que só chega com o ground truth atrasado -> a reação
# automática que ficou registrada. Cada linha usa a dimensão Window
# (baseline/shifted, os nomes dos splits congelados no CONTRATO) para separar
# as duas fases lado a lado — nunca EndpointName, porque os três endpoints
# (Real-Time/Serverless/Async) coexistem e qual deles está "em produção" é
# escolha do aluno em student/solution.yaml (Regra 4: nenhum arquivo do
# repositório pode sugerir qual pattern escolher).
#
# No máximo 3 séries por widget — mais que isso é decoração, não informação.
resource "aws_cloudwatch_dashboard" "final" {
  count = var.enable_serving ? 1 : 0

  dashboard_name = local.dashboard_name

  dashboard_body = jsonencode({
    start          = "-PT6H"
    periodOverride = "inherit"

    widgets = [
      # ------------------------------------------------------------------- #
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2
        properties = {
          markdown = join("\n", [
            "## Bora Fibra · churn-v1 em operação",
            "Leia de cima para baixo: **dados** (o mundo mudou?) -> **predições** (o modelo mudou de opinião?) -> **qualidade** (isso virou erro, quando o ground truth chega?) -> **reação** (o incidente foi aberto?).",
            "Janela `baseline` é o mundo em que churn-v1 foi treinado; janela `shifted` é a base de produção depois da mudança simulada. As duas podem contar histórias diferentes ao mesmo tempo — esse é o ponto do laboratório.",
          ])
        }
      },

      # -------------------------- linha 1: dados --------------------------- #
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 12
        height = 6
        properties = {
          title  = "Os dados ainda parecem os mesmos? (PSI máximo por feature)"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Maximum"
          period = var.alarm_period_seconds
          yAxis  = { left = { label = "PSI", showUnits = false, min = 0 } }
          annotations = {
            horizontal = [{
              label = "limiar do alarme (${var.psi_threshold})"
              value = var.psi_threshold
              color = "#d13212"
              fill  = "above"
            }]
          }
          metrics = [
            [var.metrics_namespace, "DataDriftPSIMax", "Window", "baseline", { label = "Janela baseline" }],
            [var.metrics_namespace, "DataDriftPSIMax", "Window", "shifted", { label = "Janela shifted" }],
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
          title  = "O modelo mudou de opinião? (PSI das predições)"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Maximum"
          period = var.alarm_period_seconds
          yAxis  = { left = { label = "PSI", showUnits = false, min = 0 } }
          annotations = {
            horizontal = [{
              label = "limiar do alarme (${var.psi_threshold})"
              value = var.psi_threshold
              color = "#d13212"
              fill  = "above"
            }]
          }
          metrics = [
            [var.metrics_namespace, "PredictionDriftPSI", "Window", "baseline", { label = "Janela baseline" }],
            [var.metrics_namespace, "PredictionDriftPSI", "Window", "shifted", { label = "Janela shifted" }],
          ]
        }
      },

      # ------------------------ linha 2: predições ------------------------ #
      {
        type   = "metric"
        x      = 0
        y      = 8
        width  = 12
        height = 6
        properties = {
          title  = "Está prevendo mais churn? (taxa prevista, baseline x shifted)"
          view   = "singleValue"
          region = var.aws_region
          stat   = "Average"
          period = 300
          metrics = [
            [var.metrics_namespace, "PredictedChurnRate", "Window", "baseline", { label = "Taxa prevista · baseline" }],
            [var.metrics_namespace, "PredictedChurnRate", "Window", "shifted", { label = "Taxa prevista · shifted" }],
          ]
        }
      },
      {
        type   = "alarm"
        x      = 12
        y      = 8
        width  = 12
        height = 6
        properties = {
          title = "A regra virou incidente? (estado dos dois alarmes)"
          alarms = [
            aws_cloudwatch_metric_alarm.data_drift[0].arn,
            aws_cloudwatch_metric_alarm.prediction_drift[0].arn,
          ]
        }
      },

      # ------------------------ linha 3: qualidade ------------------------ #
      {
        type   = "metric"
        x      = 0
        y      = 14
        width  = 12
        height = 6
        properties = {
          title  = "A qualidade aguentou? (F1, quando o ground truth chega)"
          view   = "singleValue"
          region = var.aws_region
          stat   = "Average"
          period = 300
          metrics = [
            [var.metrics_namespace, "ModelQualityF1", "Window", "baseline", { label = "F1 · baseline" }],
            [var.metrics_namespace, "ModelQualityF1", "Window", "shifted", { label = "F1 · shifted" }],
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
          title  = "E a ordenação? (ROC-AUC, quando o ground truth chega)"
          view   = "singleValue"
          region = var.aws_region
          stat   = "Average"
          period = 300
          metrics = [
            [var.metrics_namespace, "ModelQualityROCAUC", "Window", "baseline", { label = "ROC-AUC · baseline" }],
            [var.metrics_namespace, "ModelQualityROCAUC", "Window", "shifted", { label = "ROC-AUC · shifted" }],
          ]
        }
      },

      # -------------------------- linha 4: reação -------------------------- #
      {
        type   = "metric"
        x      = 0
        y      = 20
        width  = 12
        height = 6
        properties = {
          title     = "O sistema reagiu sozinho? (ReactionTriggered, incidentes abertos)"
          view      = "singleValue"
          region    = var.aws_region
          stat      = "Sum"
          period    = 300
          sparkline = true
          metrics = [
            [var.metrics_namespace, "ReactionTriggered", { label = "Reações disparadas" }],
          ]
        }
      },
    ]
  })
}
