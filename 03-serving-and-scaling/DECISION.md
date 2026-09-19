# Architecture Decision — Serving do churn da Bora Fibra

## Stakeholder

Helena Marques, Diretora de Receita.

## Pergunta

Qual padrão de inferência atende cada workload sem pagar complexidade ou capacidade que não precisamos?

## Evidências da nossa execução

<!-- A tabela abaixo é preenchida pelo `make resumo` com os números da SUA execução.
     Não edite à mão: o bloco entre os marcadores é regravado a cada `make resumo`.
     O que você escreve neste documento são as seções de recomendação, abaixo. -->
<!-- inicio-evidencias -->
| Workload | Padrão | O que medimos na sua execução |
|---|---|---|
| Atendimento humano | Real-Time | _rode `make compare`_ |
| App após fechamento da fatura | Serverless | _rode `make compare`_ |
| Importação de arquivo pesado | Asynchronous | _rode `make async`_ |
| Campanha noturna | Batch Transform | _rode `make batch`_ |
| Concorrência no atendimento | Real-Time sob carga | _rode `make load`_ |
| Elasticidade do atendimento | Auto Scaling | _rode `make scale-demo`_ |
<!-- fim-evidencias -->

<details>
<summary><b>💡 O que significam os números da tabela</b></summary>

| Termo | Em português claro |
|---|---|
| **p50** | Metade das chamadas respondeu nesse tempo ou menos. É o dia normal, o que a maioria sente. |
| **p95** | 95% das chamadas respondeu nesse tempo ou menos. É o dia ruim, o que 1 em cada 20 pessoas sente — e numa decisão de atendimento ele costuma pesar mais que a média, porque é dele que vem a reclamação. |
| **ms** | Milissegundo, um milésimo de segundo. Mil milissegundos são um segundo. Numa conversa ao telefone, a pessoa começa a perceber espera acima de uns 300 ms. |
| **primeira chamada** | O tempo da chamada feita depois de um período sem uso. Em alguns padrões ela é muito mais lenta que as seguintes, porque a infraestrutura precisa ser preparada antes de responder. |
| **chamadas por segundo** | Quantas respostas o endpoint entrega por segundo. Mede **vazão**, não velocidade: pode dobrar sem que cada resposta fique mais rápida — são perguntas diferentes. |
| **instância** | A máquina que executa o modelo. Mais instâncias atendem mais gente ao mesmo tempo, e cada uma custa por hora enquanto existir. |

Por que a média não aparece aqui: uma média esconde o caso ruim. Dez chamadas de 100 ms e uma de 5 segundos dão média de 545 ms, e nenhuma pessoa viveu isso — uma esperou cinco segundos. É para isso que serve o p95.

</details>

## Recomendação

Uma seção por workload. Em cada uma, responda três coisas: **o padrão serve?**, **qual custo de ociosidade você aceita** e **qual limitação você assume** ao escolher esse padrão.

### Atendimento

...

### App com rajadas

...

### Arquivo assíncrono

...

### Campanha noturna

...

## Custo do erro

O que acontece se escolhermos o padrão errado para cada workload? (pense em latência quebrada, fatura inflada, ou fila que nunca esvazia)

## Condições que fariam a decisão mudar

...
