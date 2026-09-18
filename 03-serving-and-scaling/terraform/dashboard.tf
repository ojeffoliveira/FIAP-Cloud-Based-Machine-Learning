# Dashboard do CloudWatch — a superfície visual deste laboratório.
#
# Provisionado por Terraform, e não montado à mão no console, pelos mesmos dois
# motivos do lab 04.1: some junto com o resto no `make destroy` (o `verify-clean`
# confere), e o aluno recebe exatamente o mesmo layout que o professor projeta.
#
# As quatro linhas do painel espelham as Partes do laboratório, de propósito: o
# aluno lê a linha 1 enquanto faz a Parte 4, a linha 2 na Parte 5, e assim por
# diante. Assim o painel não é um anexo decorativo, é o instrumento do passo.
#
# Regras de desenho herdadas do 04.1 (público de BI, não de plantão de SRE):
#
# * cada widget tem no título a PERGUNTA que ele responde, nunca o nome da métrica;
# * no máximo 3 séries por visualização — 15 séries não é informação, é decoração;
# * latência convertida de microssegundo para milissegundo por metric math, porque
#   a API do SageMaker devolve microssegundo e ninguém lê "449.079" como "449 ms";
# * comparação entre dois padrões de serving mora no MESMO widget, com duas séries:
#   é a comparação que é o conteúdo da aula, e ler dois gráficos lado a lado e
#   fazer a conta de cabeça é justamente o trabalho que o painel deveria poupar.
#
# Duas honestidades registradas aqui para ninguém prometer ao aluno o que o
# serviço não entrega:
#
# 1. O SageMaker NÃO publica uma métrica de "número de instâncias". A linha da
#    elasticidade é calculada: `Invocations / InvocationsPerInstance` é
#    exatamente a contagem de instâncias que atenderam a janela. Verificado por
#    `get-metric-data` contra o endpoint real (devolveu 1.0 com uma instância).
#    Consequência que o README precisa dizer: a conta só existe onde houve
#    chamada. Janela sem tráfego não desenha ponto, e isso não é defeito do
#    painel — é a definição da métrica.
# 2. `ModelSetupTime` não aparece para estes endpoints. Quem conta a história do
#    custo de subir instância é `OverheadLatency`, que existe nos dois padrões.
#
# A granularidade mínima é 60 s e o console reconsulta em intervalo próprio: isto
# é tempo QUASE real, e o README nunca promete tempo real absoluto.
resource "aws_cloudwatch_dashboard" "serving" {
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
        height = 3
        properties = {
          markdown = join("\n", [
            "## Bora Fibra · churn-v1 em quatro padrões de serving",
            "**Pergunta que o painel responde:** para cada workload, o padrão escolhido entrega a resposta no tempo que aquele workload precisa?",
            "Cada linha corresponde a uma Parte do laboratório: **linha 1 → Parte 4** (atendimento e app), **linha 2 → Parte 5** (arquivo assíncrono e campanha em batch), **linha 3 → Parte 6** (concorrência e elasticidade), **linha 4 → saúde**, que vale para o lab inteiro.",
            "Widget vazio antes do passo que gera o tráfego é o comportamento correto: a métrica só existe depois da chamada. E como cada `make` dispara uma rajada curta, a latência aparece como **pico isolado**, não como linha contínua.",
          ])
        }
      },

      # ------- linha 1 -> Parte 4: atendimento (real-time) vs app (serverless) ------- #
      {
        type   = "metric"
        x      = 0
        y      = 3
        width  = 12
        height = 6
        properties = {
          title  = "Quem responde mais rápido, atendimento ou app? (latência do modelo, ms)"
          view   = "timeSeries"
          region = var.region
          stat   = "Average"
          period = 60
          yAxis  = { left = { label = "ms", showUnits = false, min = 0 } }
          metrics = [
            [{ expression = "m1/1000", label = "Atendimento (real-time)", id = "e1", color = "#1f77b4" }],
            [{ expression = "m2/1000", label = "App (serverless)", id = "e2", color = "#ff7f0e" }],
            ["AWS/SageMaker", "ModelLatency", "EndpointName", local.realtime_endpoint_name, "VariantName", "AllTraffic", { id = "m1", visible = false }],
            ["AWS/SageMaker", "ModelLatency", "EndpointName", local.serverless_endpoint_name, "VariantName", "AllTraffic", { id = "m2", visible = false }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 3
        width  = 12
        height = 6
        properties = {
          # O overhead é onde mora o preço de não ter instância de pé: é o tempo
          # que o SageMaker gasta FORA do modelo. Comparar os dois padrões nesta
          # métrica é a forma honesta de mostrar cold start sem ModelSetupTime.
          title  = "Quanto custa não ter instância de pé? (overhead da plataforma, ms)"
          view   = "timeSeries"
          region = var.region
          stat   = "Average"
          period = 60
          yAxis  = { left = { label = "ms", showUnits = false, min = 0 } }
          metrics = [
            [{ expression = "m1/1000", label = "Atendimento (real-time)", id = "e1", color = "#1f77b4" }],
            [{ expression = "m2/1000", label = "App (serverless)", id = "e2", color = "#ff7f0e" }],
            ["AWS/SageMaker", "OverheadLatency", "EndpointName", local.realtime_endpoint_name, "VariantName", "AllTraffic", { id = "m1", visible = false }],
            ["AWS/SageMaker", "OverheadLatency", "EndpointName", local.serverless_endpoint_name, "VariantName", "AllTraffic", { id = "m2", visible = false }],
          ]
        }
      },

      # --------------- linha 2 -> Parte 5: o arquivo assíncrono --------------- #
      {
        type   = "metric"
        x      = 0
        y      = 9
        width  = 12
        height = 6
        properties = {
          # Fila e idade do mais antigo no mesmo widget, em eixos separados:
          # "quanto falta" e "há quanto tempo alguém espera" são a mesma pergunta
          # de negócio vista de dois ângulos, e separá-las em dois gráficos
          # obrigaria o aluno a correlacionar no olho.
          title  = "A fila do assíncrono está sendo drenada?"
          view   = "timeSeries"
          region = var.region
          stat   = "Maximum"
          period = 60
          yAxis = {
            left  = { label = "itens na fila", showUnits = false, min = 0 }
            right = { label = "segundos", showUnits = false, min = 0 }
          }
          metrics = [
            ["AWS/SageMaker", "ApproximateBacklogSize", "EndpointName", local.async_endpoint_name, { label = "Itens na fila", color = "#1f77b4" }],
            ["AWS/SageMaker", "ApproximateAgeOfOldestRequest", "EndpointName", local.async_endpoint_name, { label = "Espera do mais antigo (s)", color = "#d62728", yAxis = "right" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 9
        width  = 6
        height = 6
        properties = {
          # Esta é a métrica que acorda o endpoint do zero: é ela que a política
          # `async-target-from-zero` observa. Um pico aqui seguido de fila caindo
          # no widget ao lado é a história completa do escalar-a-partir-do-zero.
          title  = "Chegou trabalho sem instância para atender?"
          view   = "timeSeries"
          region = var.region
          stat   = "Maximum"
          period = 60
          yAxis  = { left = { label = "1 = sim", showUnits = false, min = 0 } }
          metrics = [
            ["AWS/SageMaker", "HasBacklogWithoutCapacity", "EndpointName", local.async_endpoint_name, { label = "Fila sem capacidade", color = "#d62728" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 18
        y      = 9
        width  = 6
        height = 6
        properties = {
          # O quarto workload. O Batch Transform não tem endpoint, então não
          # existe métrica por EndpointName: o que existe é a CPU da máquina
          # efêmera, no namespace de transform job, com dimensão `Host` no
          # formato "<nome-do-job>/<instance-id>". O nome do job carrega um
          # timestamp e muda a cada execução, então uma dimensão fixa aqui
          # quebraria no próximo `make batch` — por isso SEARCH pelo prefixo.
          #
          # O conteúdo pedagógico do widget é a FORMA da curva, não o valor: ela
          # começa, dura alguns minutos e acaba. É computação que não fica de pé.
          title  = "A máquina do batch existiu e desapareceu?"
          view   = "timeSeries"
          region = var.region
          period = 60
          yAxis  = { left = { label = "CPU %", showUnits = false, min = 0 } }
          metrics = [
            [{ expression = "SEARCH('{/aws/sagemaker/TransformJobs,Host} MetricName=\"CPUUtilization\" ${var.project_prefix}-batch', 'Average', 60)", label = "CPU do job efêmero", id = "e1" }],
          ]
        }
      },

      # ----------- linha 3 -> Parte 6: concorrência e elasticidade ----------- #
      {
        type   = "metric"
        x      = 0
        y      = 15
        width  = 12
        height = 6
        properties = {
          # O widget-âncora do lab: a elasticidade 1 -> 2 -> 1 desenhada.
          # A contagem é calculada porque o serviço não publica a métrica; ver a
          # honestidade (1) no topo deste arquivo.
          title  = "Quantas instâncias o atendimento tem agora? (1 → 2 → 1)"
          view   = "timeSeries"
          region = var.region
          stat   = "Sum"
          period = 60
          yAxis  = { left = { label = "instâncias", showUnits = false, min = 0 } }
          annotations = {
            horizontal = [{
              label = "teto do autoscaling (${var.realtime_max_capacity})"
              value = var.realtime_max_capacity
              color = "#7f7f7f"
            }]
          }
          metrics = [
            [{ expression = "m1/m2", label = "Instâncias atendendo", id = "e1", color = "#2ca02c" }],
            ["AWS/SageMaker", "Invocations", "EndpointName", local.realtime_endpoint_name, "VariantName", "AllTraffic", { id = "m1", visible = false }],
            ["AWS/SageMaker", "InvocationsPerInstance", "EndpointName", local.realtime_endpoint_name, "VariantName", "AllTraffic", { id = "m2", visible = false }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 15
        width  = 12
        height = 6
        properties = {
          # As duas linhas se separando é a distribuição acontecendo: com uma
          # instância elas coincidem; com duas, a de baixo vira metade da de cima.
          title  = "A carga se distribuiu? (chamadas no total e por instância)"
          view   = "timeSeries"
          region = var.region
          stat   = "Sum"
          period = 60
          yAxis  = { left = { label = "chamadas/min", showUnits = false, min = 0 } }
          annotations = {
            horizontal = [{
              label = "alvo da política (${var.realtime_target_invocations_per_instance} por instância)"
              value = var.realtime_target_invocations_per_instance
              color = "#ff7f0e"
            }]
          }
          metrics = [
            ["AWS/SageMaker", "Invocations", "EndpointName", local.realtime_endpoint_name, "VariantName", "AllTraffic", { label = "Total", color = "#1f77b4" }],
            ["AWS/SageMaker", "InvocationsPerInstance", "EndpointName", local.realtime_endpoint_name, "VariantName", "AllTraffic", { label = "Por instância", color = "#2ca02c" }],
          ]
        }
      },

      # ------------------ linha 4: saúde, válida para o lab todo ------------------ #
      {
        type   = "metric"
        x      = 0
        y      = 21
        width  = 8
        height = 6
        properties = {
          title     = "Alguma chamada falhou?"
          view      = "singleValue"
          region    = var.region
          stat      = "Sum"
          period    = 300
          sparkline = true
          metrics = [
            ["AWS/SageMaker", "Invocation5XXErrors", "EndpointName", local.realtime_endpoint_name, "VariantName", "AllTraffic", { label = "5XX atendimento" }],
            ["AWS/SageMaker", "Invocation5XXErrors", "EndpointName", local.serverless_endpoint_name, "VariantName", "AllTraffic", { label = "5XX app" }],
            ["AWS/SageMaker", "InvocationModelErrors", "EndpointName", local.realtime_endpoint_name, "VariantName", "AllTraffic", { label = "Erros do modelo" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 21
        width  = 8
        height = 6
        properties = {
          # O serverless não tem instância para medir CPU; o teto dele é
          # concorrência. Esta é a métrica equivalente a "está apertado?".
          title  = "O app está perto do teto de concorrência? (%)"
          view   = "timeSeries"
          region = var.region
          stat   = "Maximum"
          period = 60
          yAxis  = { left = { label = "%", showUnits = false, min = 0 } }
          metrics = [
            ["AWS/SageMaker", "ServerlessConcurrentExecutionsUtilization", "EndpointName", local.serverless_endpoint_name, "VariantName", "AllTraffic", { label = "Uso da concorrência", color = "#ff7f0e" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 21
        width  = 8
        height = 6
        properties = {
          # CPU vem de outro namespace (host metrics) e existe enquanto a
          # instância estiver de pé, com ou sem tráfego. No async ela cai para
          # "sem dado" quando a capacidade volta a zero, e isso é a evidência
          # visual de que escalar-para-zero realmente desligou a máquina.
          title  = "As instâncias estão de pé? (CPU %)"
          view   = "timeSeries"
          region = var.region
          stat   = "Average"
          period = 60
          yAxis  = { left = { label = "%", showUnits = false, min = 0 } }
          metrics = [
            ["/aws/sagemaker/Endpoints", "CPUUtilization", "EndpointName", local.realtime_endpoint_name, "VariantName", "AllTraffic", { label = "Atendimento", color = "#1f77b4" }],
            ["/aws/sagemaker/Endpoints", "CPUUtilization", "EndpointName", local.async_endpoint_name, "VariantName", "AllTraffic", { label = "Assíncrono", color = "#9467bd" }],
          ]
        }
      },
    ]
  })
}
