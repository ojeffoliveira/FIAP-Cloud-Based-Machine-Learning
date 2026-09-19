# Decisão de release — SLM de retenção da Bora Fibra

Documento para Helena Marques, diretora de receita. Escreva como se ela fosse ler — porque, na prática, é ela quem lê. O exercício não é descrever o que aconteceu; é defender uma recomendação com a evidência que você tem, e declarar o que você **não** faria ainda.

## Evidências da nossa execução

<!-- A tabela abaixo é preenchida pelo `make resumo` com os números da SUA execução.
     Não edite à mão: o bloco entre os marcadores é regravado a cada `make resumo`.
     O que você escreve neste documento são as seções de recomendação, abaixo. -->
<!-- inicio-evidencias -->
| O que valida | Evidência | O que medimos na sua execução |
|---|---|---|
| Fumaça V1 | `smoke-v1.json` | **8/8** casos de fumaça responderam corretamente no endpoint V1. |
| Qualidade V1 (gate generativo) | `evaluation-v1.json` | **0/8** casos passaram no gate generativo de V1 (idioma, tamanho, contrato de saída), com **0** violação(ões) de PII ou valor inventado. |
| Qualidade V2 (gate generativo) | `evaluation-v2.json` | _rode workflow `04-2-deploy-v2.yml`_ |
| Desempenho V1 × V2 (latência e tokens/s) | `benchmark.json` | V1: metade das respostas saiu em até **2,6 segundos**, 95% em até **2,6 segundos**, a **24,8 tokens/s**. V2 ainda não tem benchmark próprio — a chave `v2` do arquivo nasce no workflow de deploy V2. |
| Credencial do deploy V2 | `credential-source-v2.json` | _rode make pipeline-preflight (dentro do workflow)_ |
| Execução do workflow V2 | `workflow-run.json` | _rode workflow `04-2-deploy-v2.yml`_ |
<!-- fim-evidencias -->

<details>
<summary><b>💡 O que significam os termos da tabela</b></summary>

| Termo | Em português claro |
|---|---|
| **Quantização** | Compressão do modelo original para caber e rodar mais rápido em CPU. `Q4_0` (V1) e `Q4_K_M` (V2) guardam cada peso em ~4 bits, mas com técnicas de arredondamento diferentes — `Q4_K_M` costuma preservar mais qualidade ao custo de um arquivo um pouco maior. Não é o modelo "aprendendo mais": é o mesmo conhecimento, guardado com mais ou menos perda. |
| **p50** | Metade das chamadas respondeu nesse tempo ou menos. É o dia normal, o que a maioria sente. |
| **p95** | 95% das chamadas respondeu nesse tempo ou menos. É o dia ruim, o que 1 em cada 20 chamadas sente — numa recomendação lida durante uma ligação, é ele que decide se o atendente espera em silêncio ou não. |
| **ms / segundos** | Milissegundo é um milésimo de segundo; mil milissegundos são um segundo. Abaixo de 1.000 ms a tabela mostra milissegundos, acima disso troca para segundos — "2,6 segundos" se lê mais rápido que "2624,9 ms". |
| **Tokens por segundo** | Quantos pedaços de palavra o modelo produz por segundo de geração. Mede **vazão de escrita**, não o tempo até a primeira palavra — os dois números respondem perguntas diferentes. |
| **Gate generativo** | A bateria de checagens que decide se uma resposta do SLM é aceitável: idioma, tamanho, JSON no schema esperado, ausência de PII ou valor inventado, tempo dentro do envelope medido. Não é nota de qualidade de texto — é conformidade de contrato de saída. |
| **PII / valor inventado** | O modelo citou um nome, CPF, telefone, desconto ou prazo que não estava no contexto enviado a ele. Qualquer ocorrência reprova o gate, sem exceção — é a única regra dura do lab (spec §8). |
| **Credencial `iam-role` / instance profile** | A forma como o job AWS se autenticou. `iam-role` (instance profile da EC2, resolvido via IMDSv2) prova que nenhuma chave estática (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`) passou pelo GitHub — é a exigência não negociável da Helena. |

**Por que este lab nunca compara o texto gerado com uma resposta "gabarito":** um SLM de 0,5B não produz a mesma frase duas vezes, nem entre chamadas do mesmo release, nem (principalmente) entre V1 e V2 com quantizações diferentes. Exigir texto idêntico reprovaria respostas corretas e criaria uma falsa sensação de precisão determinística que o modelo não tem. Por isso o gate mede propriedades estruturais e verificáveis (idioma, PII, tamanho, tempo) em vez de comparar caractere a caractere com um exemplo fixo.

</details>

## Problema

- Qual é a demanda exata da Helena, em uma frase, sem jargão de ML?
- Por que "cada atendente traduz o score de um jeito" é um problema de negócio, não só de UX? O que isso já custou (campanhas erradas, inconsistência), mesmo sem número exato?
- O que o SLM **não** faz aqui (decidir churn, substituir `churn-v1`, acessar PII)? Por que essa fronteira importa para a Helena, não só para quem programou?

## Release V1

- Que modelo-base, quantização e engine de inferência sustentam V1? Onde isso está registrado de forma imutável (`model/releases/v1.yaml`)?
- Quem executou o deploy de V1, com que credencial, e de onde (que ambiente)?
- V1 respondeu em português, dentro do contrato de saída (2-3 frases, sem inventar dado)? A linha "Fumaça V1" da tabela acima é a prova — se ela ainda estiver pendente, rode `make smoke-v1` antes de responder.

## Release V2

- O que muda de V1 para V2 no artefato (quantização) e no processo de entrega (quem executa, onde, com que credencial)?
- V2 foi testada antes de ser promovida a recomendada? O que aconteceria se V2 tivesse falhado nesse teste — o que a Helena teria visto, e o que ela não teria visto?

## Evidência de qualidade

- Quantos dos casos de `eval/cases.yaml` passaram em V1? E em V2? As linhas "Qualidade V1" e "Qualidade V2" da tabela acima trazem o placar e as violações de PII de cada release.
- Alguma resposta de V1 ou V2 inventou um dado que não estava no contexto (nome, valor de desconto, CPF)? Se sim, o que isso significaria em produção real?

## Evidência de performance

- A linha "Desempenho V1 × V2" da tabela acima traz p50/p95 e tokens/s medidos. A diferença observada é explicada pela quantização, pelo tamanho do contexto, ou por outra variável?
- O envelope de latência aceito para este caso de uso (recomendação para o atendente durante uma ligação) é compatível com o que foi medido? Por que sim ou por que não?

## Evidência de segurança do pipeline

- As linhas "Credencial do deploy V2" e "Execução do workflow V2" da tabela acima trazem a prova de que o job que tocou AWS nunca usou um secret AWS. Se ainda estiverem pendentes, isso por si só já é uma resposta parcial: o que falta rodar?
- Quais das seis mitigações de "self-hosted runner em repo público" você consegue apontar no workflow real (`workflow_dispatch` apenas, condição de actor/ref/confirm, runner ephemeral, `GITHUB_TOKEN` restrito, `persist-credentials: false`, actions pinadas por SHA)? Alguma delas faltou ou foi só parcial?

## O que muda de V1 para V2

- Além da quantização, o que muda no *processo* (quem decide fazer o deploy, quantos cliques/comandos humanos existem no caminho, o que fica registrado automaticamente que antes dependia de alguém lembrar de anotar)?
- Essa mudança de processo reduz ou aumenta o risco de um deploy errado chegar à Helena sem ela saber? Por quê?

## Release recomendada

- V1 ou V2? A recomendação está sustentada pelas seções de qualidade e performance acima, ou por outra razão (ex.: preferir o processo mais auditável mesmo com performance parecida)? Diga qual.
- Existe algum cenário em que a resposta certa seria "nenhuma das duas, ainda"? O que precisaria ser verdade para essa resposta se aplicar?

## Risco residual

- Mesmo com a release recomendada em produção, o que continua sem prova? (ex.: qualidade medida só em casos sintéticos, sem interação real de atendente; runner self-hosted como adaptação ao Academy, não a arquitetura recomendada fora dele)
- Qual desses riscos residuais precisaria de mitigação antes de uma escala maior que a deste laboratório?

## Condição de rollback

- Qual métrica, medida sobre qual volume mínimo e por quanto tempo, dispararia a volta para a release anterior? (Um gatilho sem volume mínimo nem duração não é executável.)
- Quem tem autoridade para decidir o rollback, e o que acontece com o tráfego durante a decisão?

## O que eu faria diferente fora do AWS Academy

- Qual parte desta arquitetura é uma adaptação explícita às restrições do Academy (IAM restrito, sem GPU, sem OIDC) e não a escolha que você faria com uma conta de produção sem essas restrições?
- Se fosse reescrever a autenticação do pipeline com IAM livre, o que mudaria concretamente (nomeie o mecanismo, não só "seria mais seguro")?
