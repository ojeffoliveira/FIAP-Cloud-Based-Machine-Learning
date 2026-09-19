# Decisão final — operação de retenção da Bora Fibra

> Execução sem decisão não conclui o trabalho. Decisão sem evidência também não conclui o
> trabalho. Este documento é o memorando que a Helena Marques, diretora de receita, vai ler
> antes do go-live de segunda-feira — escreva para ela, não para o professor.
>
> As perguntas-guia de cada seção existem para orientar o raciocínio, não para serem
> respondidas uma por uma como um formulário: escreva em prosa curta e cite o arquivo de
> `artifacts/evidence/` que sustenta cada afirmação. Substitua todo `<preencher>` — um
> `<preencher>` esquecido é tratado como seção não respondida.

**Equipe:** <preencher>
**Data:** <preencher>

## Evidências da nossa execução

<!-- A tabela abaixo é preenchida pelo `make resumo` com os números da SUA execução.
     Não edite à mão: o bloco entre os marcadores é regravado a cada `make resumo`.
     O que vocês escrevem neste documento são as seções de decisão abaixo — a
     tabela é só o dado bruto para vocês citarem nelas. -->
<!-- inicio-evidencias -->
| Elo da cadeia | Fonte | O que medimos na sua execução |
|---|---|---|
| Treino e artefato | `training.json + artifact.json` | _rode `make deploy`_ |
| Atendimento e campanha | `atendimento.json + campanha.json` | _rode `make run`_ |
| Janela saudável | `baseline-drift.json` | _rode `make baseline`_ |
| Janela deslocada | `production-drift.json` | _rode `make drift`_ |
| Alarme e reação automática | `alarm.json + reaction.json` | _rode `make alarm-status`_ |
| Qualidade com ground truth | `quality.json` | _rode `make ground-truth`_ |
<!-- fim-evidencias -->

<details>
<summary>💡 O que significam os números da tabela</summary>

| Termo | Em português claro |
|---|---|
| **PSI** (Population Stability Index) | Mede o quanto a distribuição de uma variável (ou das predições) mudou entre a referência e a janela observada. Não olha rótulo, só a forma do histograma. Abaixo de ~0,10 é ruído de amostragem; acima do limiar (0,20 neste trabalho) é mudança que merece atenção. |
| **Janela baseline vs. shifted** | `baseline` é a janela "saudável", parecida com o mundo em que `churn-v1` foi treinado. `shifted` é a janela depois do reajuste de preço e da mudança de política comercial. As duas podem contar histórias diferentes ao mesmo tempo. |
| **Drift de dados vs. drift de predição** | O primeiro mede se as features de entrada mudaram de distribuição; o segundo mede se a saída do modelo (a probabilidade prevista) mudou. Um pode se mover sem o outro. |
| **Ground truth** | O rótulo verdadeiro (o cliente cancelou ou não), que só chega depois da predição. Sem ele não existe F1 nem ROC-AUC — só PSI, que não olha acerto, só distribuição. |
| **F1** | Média entre precisão e recall. Cai quando o modelo passa a errar mais, para qualquer lado (prevendo churn demais ou de menos). A matriz de confusão é que diz **para qual lado**. |
| **ROC-AUC** | O quão bem o modelo ordena clientes por risco, independente do ponto de corte escolhido. 0,50 é chute puro; 1,00 é ordenação perfeita. |
| **Falso positivo / falso negativo** | Falso positivo: o modelo previu cancelamento e o cliente não ia cancelar (custo: ação de retenção desnecessária). Falso negativo: o modelo não viu risco e o cliente cancelou de fato (custo: receita perdida sem qualquer tentativa de retenção). |
| **Padrão de serving** (Real-Time / Serverless / Async / Batch) | A forma como o modelo é consultado — nunca muda o modelo em si, só como a predição chega até quem precisa dela. |
| **p50 / p95** | Metade das chamadas respondeu em até o p50; 95% respondeu em até o p95. Aparecem em `candidates.md`, não nesta tabela — o comparativo de latência é gerado por `make compare`, antes de vocês escolherem o pattern. |

Por que a média não aparece: dez chamadas de 100 ms e uma de 5 segundos dão média de
545 ms, e ninguém viveu essa experiência — nem a rápida, nem a lenta. p50/p95 descrevem
o que a maioria de fato sentiu; a média só esconde o outlier.

</details>

## Resumo executivo

> *Em três ou quatro frases, sem jargão de ML: o que vocês decidiram para atendimento e
> para campanha, o estado do modelo hoje, e se o go-live está liberado ou condicionado a
> algo. Alguém que só ler este parágrafo precisa entender a decisão inteira.*

<preencher>

## Matriz de decisão

> *Preencha as duas linhas com o padrão escolhido em `student/solution.yaml`, o requisito
> do workload que pesou mais, o arquivo de evidência exato (`artifacts/evidence/atendimento.json`,
> `campanha.json`, `candidates.json` ou `candidates.md`) e o trade-off que vocês aceitaram
> conscientemente ao não escolher a outra opção.*

| Workload | Requisito | Escolha | Evidência | Trade-off aceito |
|---|---|---|---|---|
| Atendimento | <preencher> | <preencher> | <preencher> | <preencher> |
| Campanha | <preencher> | <preencher> | <preencher> | <preencher> |

## Estado do modelo antes do go-live

> *O que a evidência de treino e artifact mostra sobre o modelo que está prestes a entrar
> em produção — linhagem, quando foi treinado, com qual dataset de referência. Aponte
> `artifacts/evidence/training.json` e `artifact.json`. O que essas evidências provam, e o
> que elas não têm como provar sobre o comportamento em produção?*

<preencher>

## Evidência de drift

> *Qual foi o PSI máximo e em qual feature, segundo `artifacts/evidence/baseline-drift.json`
> e `production-drift.json`? Quantas features cruzaram o limiar de 0,20 e quantas ficaram
> abaixo — isso diz algo? O PSI de predição se moveu, e em que direção? Que mudança de
> política comercial ou de preço da Bora Fibra explica o deslocamento observado, e o alarme
> em `alarm.json` registrou isso?*

<preencher>

## Evidência de qualidade

> *O que `artifacts/evidence/quality.json` mostra sobre F1 e ROC-AUC quando o ground truth
> chegou? Olhando a matriz de confusão, o modelo passou a errar mais para que lado — falso
> positivo ou falso negativo? A queda de qualidade observada foi material ou marginal,
> dado o tamanho da amostra?*

<preencher>

## Custo do erro

> *Traduza os dois modos de falha em impacto de negócio da Bora Fibra, sem jargão: quanto
> custa acionar a equipe de retenção para um cliente que não ia sair (ligação desnecessária,
> desconto oferecido à toa, incômodo)? E quanto custa deixar passar, sem qualquer ação, um
> cliente que ia sair de fato (receita perdida, cliente que some sem aviso)? Os dois erros
> custam o mesmo para a operação? Essa comparação está sustentada pela matriz de confusão
> de `quality.json`, ou é uma suposição do grupo?*

<preencher>

## Decisão de go-live

> *Diante da evidência de drift e de qualidade acima, o modelo está liberado para a
> operação piloto de segunda-feira, liberado com restrição, ou não liberado ainda? Uma
> decisão de go-live que não cita a evidência das duas seções anteriores não é uma
> decisão — é uma opinião.*

<preencher>

## Ação para as próximas 24 horas

> *O que alguém da Bora Fibra faz nas próximas 24 horas, e por quê. Seja específico: uma
> ação que não diz quem faz o quê não é uma ação. Se a resposta for "nada", justifique com
> a mesma evidência das seções anteriores.*

<preencher>

## O que não automatizaríamos

> *A automação deste trabalho detecta drift, abre incidente e mede qualidade — mas não
> retreina e não troca o modelo em produção sozinha. Por que essa fronteira é a certa hoje?
> O que precisaria existir (volume de ground truth, histórico de decisões corretas, revisão
> humana) para vocês confiarem em uma reação automática mais forte que essa?*

<preencher>

## Condição para retraining

> *Escreva o gatilho como uma regra que outra pessoa, sem consultar vocês, consegue
> executar: qual métrica, acima ou abaixo de qual limiar, medida por quanto tempo, com que
> volume mínimo de dados, e quem tem autoridade para aprovar. Formato esperado (o exemplo
> usa uma métrica que não é nenhuma das deste trabalho, só para ilustrar a forma):
> "taxa de erro 5xx do endpoint acima de 1% por 3 dias consecutivos, aprovação do time de
> plataforma". Um gatilho do tipo "quando o modelo piorar" não é um gatilho.*

<preencher>

## Condição para rollback

> *Se uma nova versão do modelo fosse promovida, o que faria vocês voltar para a versão
> anterior? Qual métrica, medida sobre qual janela de tempo e comparada com qual
> referência, dispararia essa decisão — e quem decide? Mesmo formato executável da seção
> anterior: métrica, limiar, janela, responsável.*

<preencher>

## Limitações

> *O que este trabalho não prova. Pense no tamanho das amostras de atendimento e campanha,
> no fato de o ground truth ter sido gerado para o exercício e não coletado em produção
> real, no número de execuções de `make run`, e no que uma janela curta de observação
> autoriza ou não vocês a concluir.*

<preencher>

## Pergunta que ainda precisamos responder para Helena

> *Depois de toda a evidência reunida, qual é a pergunta que continua sem resposta — a que
> vocês fariam para a Helena antes de assinar o go-live em definitivo? Uma pergunta boa
> aponta para uma lacuna real de evidência, não para um detalhe cosmético.*

<preencher>
