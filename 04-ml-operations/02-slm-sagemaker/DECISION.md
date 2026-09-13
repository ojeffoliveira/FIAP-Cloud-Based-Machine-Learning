# Decisão de release — SLM de retenção da Bora Fibra

Documento para Helena Marques, diretora de receita. Escreva como se ela fosse ler — porque, na prática, é ela quem lê. O exercício não é descrever o que aconteceu; é defender uma recomendação com a evidência que você tem, e declarar o que você **não** faria ainda.

## Problema

- Qual é a demanda exata da Helena, em uma frase, sem jargão de ML?
- Por que "cada atendente traduz o score de um jeito" é um problema de negócio, não só de UX? O que isso já custou (campanhas erradas, inconsistência), mesmo sem número exato?
- O que o SLM **não** faz aqui (decidir churn, substituir `churn-v1`, acessar PII)? Por que essa fronteira importa para a Helena, não só para quem programou?

## Release V1

- Que modelo-base, quantização e engine de inferência sustentam V1? Onde isso está registrado de forma imutável (`model/releases/v1.yaml`)?
- Quem executou o deploy de V1, com que credencial, e de onde (que ambiente)?
- V1 respondeu em português, dentro do contrato de saída (2-3 frases, sem inventar dado)? Aponte o arquivo de evidência que prova isso (`smoke-v1.json`).

## Release V2

- O que muda de V1 para V2 no artefato (quantização) e no processo de entrega (quem executa, onde, com que credencial)?
- V2 foi testada antes de ser promovida a recomendada? O que aconteceria se V2 tivesse falhado nesse teste — o que a Helena teria visto, e o que ela não teria visto?

## Evidência de qualidade

- Quantos dos casos de `eval/cases.yaml` passaram em V1? E em V2? O gate generativo mediu o quê exatamente (aponte para `evaluation-v1.json`/`evaluation-v2.json`) — e por que ele não comparou texto gerado com um texto "gabarito"?
- Alguma resposta de V1 ou V2 inventou um dado que não estava no contexto (nome, valor de desconto, CPF)? Se sim, o que isso significaria em produção real?

## Evidência de performance

- Qual foi p50/p95 de latência e tokens/s (se medido) de V1 e V2? Aponte `benchmark.json`. A diferença observada é explicada pela quantização, pelo tamanho do contexto, ou por outra variável?
- O envelope de latência aceito para este caso de uso (recomendação para o atendente durante uma ligação) é compatível com o que foi medido? Por que sim ou por que não?

## Evidência de segurança do pipeline

- Aponte, com o arquivo/campo exato, a prova de que o job que tocou AWS nunca usou um secret AWS (`credential-source-v2.json`, `workflow-run.json`).
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
