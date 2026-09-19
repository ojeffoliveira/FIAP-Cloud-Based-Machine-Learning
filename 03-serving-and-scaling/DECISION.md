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
| Workload | Padrão | Evidência medida na sua execução |
|---|---|---|
| Atendimento humano | Real-Time | _rode `make compare`_ |
| App após fechamento da fatura | Serverless | _rode `make compare`_ |
| Importação de arquivo pesado | Asynchronous | _rode `make async`_ |
| Campanha noturna | Batch Transform | _rode `make batch`_ |
| Concorrência no atendimento | Real-Time sob carga | _rode `make load`_ |
| Elasticidade do atendimento | Application Auto Scaling | _rode `make scale-demo`_ |
<!-- fim-evidencias -->

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
