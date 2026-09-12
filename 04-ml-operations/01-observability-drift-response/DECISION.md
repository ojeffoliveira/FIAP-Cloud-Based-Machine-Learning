# Decisão operacional — Bora Fibra · churn-v1

> Preencha depois do passo 21. As perguntas-guia de cada seção existem para orientar,
> não para serem respondidas uma por uma: escreva em prosa curta, como um memorando que
> a Helena Marques (diretora de receita) vai ler em cinco minutos.
>
> Cite a evidência. Toda afirmação sua deve poder apontar para um arquivo em
> `artifacts/evidence/` — é para isso que o dossiê existe.

**Autor:**
**Data:**
**Sistema:** endpoint de churn da Bora Fibra, linhagem `churn-v1`

---

## Estado observado

*O que estava acontecendo quando você chegou? Em duas ou três frases, sem jargão — como
você descreveria a situação para alguém que não abriu o dashboard.*

## Evidência de infraestrutura

*O que as métricas de infraestrutura diziam? Invocações, erros 4XX/5XX, latência, estado
do endpoint. E a pergunta que importa: o que essas métricas provaram, e o que elas não
tinham como provar?*

## Evidência de drift

*Qual foi o PSI máximo e em qual feature? Quantas features cruzaram o limiar de 0,20?
Quais ficaram abaixo, e isso te diz algo? Que decisão de negócio da Bora Fibra explica
cada deslocamento? E o PSI das predições — o modelo mudou de opinião, e em que direção?*

## Evidência de qualidade

*O que aconteceu com F1 e ROC-AUC quando o ground truth chegou? Olhe a matriz de
confusão das duas janelas: o modelo passou a errar mais para que lado? Falso positivo ou
falso negativo dominou? A queda foi material ou marginal?*

## Custo do erro

*Traduza o modo de falha em dinheiro e em experiência do cliente. Quanto custa disparar
retenção para um cliente que ia ficar? E quanto custa deixar passar um que ia sair? Os
dois erros custam o mesmo para a Bora Fibra? Qual deles a empresa prefere cometer?*

## Ação recomendada

*O que você recomenda fazer nas próximas 24 horas, e por quê. Seja específico: uma
recomendação que não diz quem faz o quê não é uma recomendação.*

## O que NÃO automatizaríamos ainda

*A Lambda deste lab abre incidente e segura promoção. Ela poderia ter disparado um
retraining automático. Por que não deveria? Que condições precisariam existir para você
confiar em uma reação automática mais forte que essa?*

## Condição para retraining

*Escreva o gatilho como uma regra que outra pessoa consegue executar sem te consultar.
Que evidência precisa existir? Por quanto tempo? Com qual volume de ground truth? Quem
aprova? Um gatilho que diz "quando o modelo piorar" não é um gatilho.*

## Condição para rollback

*Se `churn-v2` for treinada e promovida, o que faria você voltar para `churn-v1`? Qual
métrica, medida em quanto tempo, comparada com qual referência?*

## Limitações desta análise

*O que este laboratório não provou. Pense no tamanho das janelas, no fato de o ground
truth ter sido simulado, no número de execuções, e no que uma amostra de 200 clientes
autoriza ou não a concluir.*
