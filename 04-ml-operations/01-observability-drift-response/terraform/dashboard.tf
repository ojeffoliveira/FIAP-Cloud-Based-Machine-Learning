# Dashboard do CloudWatch — a superfície que a aula lê durante o lab inteiro.
#
# Ele é provisionado por Terraform e não montado à mão no console por dois
# motivos: some junto com o resto no `make destroy` (o `verify-clean` confere), e o
# aluno recebe o mesmo layout que o professor projeta na tela.
#
# Público: analista de BI, não plantonista de SRE. Regras de desenho aplicadas
# aqui (validadas contra a AWS real no lab 03, replicadas sem variação):
#
# * cada widget tem no título a PERGUNTA que ele responde, não o nome da métrica;
# * no máximo 3 séries por visualização — mais que isso é decoração, não leitura;
# * a comparação (baseline x drift, 4XX x 5XX) mora DENTRO do mesmo widget, para
#   o leitor não fazer a conta de cabeça entre dois gráficos;
# * latência convertida de microssegundo para milissegundo por metric math: a API
#   do SageMaker devolve microssegundo, e ninguém lê "1.240.000" como "1,2 s";
# * PSI já nasce adimensional (não passa por metric math de unidade), então o
#   eixo Y só recebe o rótulo "PSI" para o leitor não confundir com porcentagem;
# * o limiar do alarme entra como linha anotada (`annotations.horizontal`), para
#   "cruzou" ser visível em vez de calculado de cabeça;
# * `singleValue` + `sparkline` é reservado para "está no ar?" e contagens
#   (invocações, erros, reações) — o que importa é o número agora e a tendência
#   recente. `timeSeries` é para o que precisa mostrar EVOLUÇÃO no tempo (latência,
#   PSI). Comparações de janela fechada (baseline x drift em taxa, F1, ROC-AUC) são
#   `singleValue` SEM sparkline: não existe "tendência" dentro de uma janela só.
#
# Nome do painel: `local.dashboard_name` (definido em locals.tf) já leva
# `${local.suffix}`, o mesmo sufixo aleatório dos outros recursos do ciclo de
# vida. NÃO trocar para um nome fixo: dois ciclos (`make apply` → `make destroy`
# → `make apply` de novo) na mesma conta sobrescreveriam o painel um do outro, e
# o painel abriria mostrando zero sem nada na tela avisar que é o painel "errado".
# Isso já aconteceu de verdade neste curso (ver regra 10 da spec de padrão visual)
# e é por isso que o nome nunca é uma constante literal aqui.
#
# Atualização: a granularidade mínima de métrica customizada é 60 s, e o console
# refaz a consulta em intervalo próprio. Isso é **tempo quase real** — o README
# nunca promete tempo real absoluto, porque não é o que o serviço entrega.
resource "aws_cloudwatch_dashboard" "mlops" {
  count = var.deploy_serving ? 1 : 0

  dashboard_name = local.dashboard_name

  dashboard_body = jsonencode({
    # Janela do painel inteiro, não de cada widget (regra 9 da spec): o lab inteiro
    # cabe numa aula de poucas horas, então 1h é curta o bastante para não pedir
    # scroll no tempo e longa o bastante para conter baseline + drift + ground
    # truth sem o aluno precisar reabrir o link.
    start          = "-PT1H"
    periodOverride = "inherit"

    widgets = [
      # ------------------------------------------------------------------- #
      { # Cabeçalho: amarra cada faixa do painel à Parte do README em que ela
        # ganha dado, e avisa que faixa vazia é honestidade, não bug. Sem isso o
        # aluno interpreta "gráfico em branco" como falha de coleta.
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 4
        properties = {
          markdown = join("\n", [
            "## Bora Fibra · churn-v1 em operação",
            "**Pergunta que este painel responde:** o modelo ainda enxerga o mundo em que foi treinado — e, quando parar de enxergar, alguém percebe?",
            "**Ordem de leitura, faixa por faixa, amarrada às Partes do README:**",
            "1. **Faixa 1 · Infraestrutura** (Parte 3) — o endpoint está de pé, sem erro, na latência esperada.",
            "2. **Faixa 2 · Dados** (Parte 4) — a entrada ainda parece o treino, feature a feature.",
            "3. **Faixa 3 · Predições** (Parte 4) — o modelo mudou de opinião sobre quem vai sair.",
            "4. **Faixa 4 · Qualidade** (Parte 6) — só existe depois que o rótulo verdadeiro chega; fica vazia até lá.",
            "5. **Faixa 5 · Reação** (Parte 5) — a regra virou incidente, e alguém (ou algo) foi avisado.",
            "**Widget vazio antes do passo que gera tráfego é o comportamento correto** — não há dado a mostrar ainda, não uma falha de coleta.",
          ])
        }
      },

      # ---------------------- linha 1: infraestrutura (Parte 3) ----------- #
      {
        type   = "metric"
        x      = 0
        y      = 4
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
        y      = 4
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
        y      = 4
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
            # SageMaker devolve ModelLatency/OverheadLatency em MICROssegundo; a
            # divisão por 1000 é feita aqui, não no Python, para o widget mostrar
            # "ms" mesmo que alguém troque a fonte da métrica depois. As duas
            # séries brutas (m1/m2) ficam invisíveis: só a versão convertida
            # aparece, senão o widget mostraria os dois eixos de grandeza juntos.
            [{ expression = "m1/1000", label = "Latência do modelo (ms)", id = "e1" }],
            [{ expression = "m2/1000", label = "Overhead da plataforma (ms)", id = "e2" }],
            ["AWS/SageMaker", "ModelLatency", "EndpointName", local.endpoint_name, "VariantName", "AllTraffic", { id = "m1", visible = false }],
            ["AWS/SageMaker", "OverheadLatency", "EndpointName", local.endpoint_name, "VariantName", "AllTraffic", { id = "m2", visible = false }],
          ]
        }
      },

      # -------------------------- linha 2: dados (Parte 4) ---------------- #
      {
        type   = "metric"
        x      = 0
        y      = 10
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
            # Mesma métrica (DataDriftPSIMax), duas dimensões de Window: é a
            # comparação vivendo no mesmo widget (regra 3), não dois gráficos que o
            # leitor precisaria sobrepor de cabeça.
            [var.metrics_namespace, "DataDriftPSIMax", "EndpointName", local.endpoint_name, "Window", "baseline", { label = "Janela baseline" }],
            [var.metrics_namespace, "DataDriftPSIMax", "EndpointName", local.endpoint_name, "Window", "drift", { label = "Janela com drift" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 10
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
            # O dataset tem 7 features monitoradas (ver src/), mas o limite de 3
            # séries por widget (regra 2) obriga a escolher. As 3 aqui são as que
            # mais cruzam o limiar na janela de drift do próprio README
            # (support_calls_90d 2.87, monthly_charges 1.60, usage_score 0.64,
            # nessa ordem — as outras 4 ficam entre 0.01 e 0.46). Não é SEARCH com
            # limite porque a lista de features é fixa e pequena; SEARCH faria
            # sentido se o número de features fosse dinâmico. As 7 continuam
            # publicadas e disponíveis via Metrics Explorer para quem quiser abrir.
            for feature in ["support_calls_90d", "monthly_charges", "usage_score"] :
            [var.metrics_namespace, "DataDriftPSI_${feature}", "EndpointName", local.endpoint_name, { label = feature }]
          ]
        }
      },

      # ------------------------ linha 3: predições (Parte 4) -------------- #
      {
        type   = "metric"
        x      = 0
        y      = 16
        width  = 8
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
        # Antes este widget somava taxa prevista E probabilidade média das duas
        # janelas no mesmo lugar — 4 séries, acima do limite da regra 2. Separado
        # em dois widgets de 2 séries cada: cada um já é uma comparação completa
        # (baseline x drift) e nenhum decora com uma métrica que não é o assunto
        # do título. Efeito colateral: o painel passa de 12 para 13 widgets no
        # total — quem mantém o número "12" no README (passo 9 e checklist da
        # Parte 3) precisa atualizar esse valor.
        type   = "metric"
        x      = 8
        y      = 16
        width  = 8
        height = 6
        properties = {
          title  = "Está prevendo mais churn? (taxa prevista, baseline x drift)"
          view   = "singleValue"
          region = var.region
          stat   = "Average"
          period = 300
          metrics = [
            [var.metrics_namespace, "PredictedChurnRate", "EndpointName", local.endpoint_name, "Window", "baseline", { label = "Taxa prevista · baseline" }],
            [var.metrics_namespace, "PredictedChurnRate", "EndpointName", local.endpoint_name, "Window", "drift", { label = "Taxa prevista · drift" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 16
        width  = 8
        height = 6
        properties = {
          # Taxa e probabilidade média respondem perguntas diferentes: a taxa é
          # "quantos" (decisão em 0,5), a probabilidade média é "quão convicto" —
          # por isso ficam em widgets separados em vez de um só rotulado com "e".
          title  = "Com que confiança? (probabilidade média, baseline x drift)"
          view   = "singleValue"
          region = var.region
          stat   = "Average"
          period = 300
          metrics = [
            [var.metrics_namespace, "MeanChurnProbability", "EndpointName", local.endpoint_name, "Window", "baseline", { label = "Prob. média · baseline" }],
            [var.metrics_namespace, "MeanChurnProbability", "EndpointName", local.endpoint_name, "Window", "drift", { label = "Prob. média · drift" }],
          ]
        }
      },

      # ------------------------ linha 4: qualidade (Parte 6) --------------- #
      # Faixa inteira fica vazia até o `make ground-truth` do passo 19: o
      # SageMaker/CloudWatch não têm como saber F1 ou ROC-AUC sem rótulo real, e
      # aqui o rótulo verdadeiro só chega dias depois da predição (ver README).
      {
        type   = "metric"
        x      = 0
        y      = 22
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
        y      = 22
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

      # -------------------------- linha 5: reação (Parte 5) ---------------- #
      {
        type   = "alarm"
        x      = 0
        y      = 28
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
        y      = 28
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
