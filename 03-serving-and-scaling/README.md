# 03 - Serving and Scaling

<!--
CONVENÇÃO DE PRINTS DESTE README (nota para o professor, não aparece renderizada)

Este laboratório é executado em aula e não tem entrega em zip, então print NUNCA é
tarefa do aluno: é imagem que o autor captura ao validar o lab de ponta a ponta e
embute no README, para o aluno comparar a tela dele com a que deveria aparecer.

Cada bloco "> 📸 Print" abaixo marca o lugar exato onde a imagem entra, com o que
capturar e o que aquela imagem prova. Depois de salvar o arquivo em `img/`, troque
o bloco inteiro pela linha de imagem que está comentada logo abaixo dele.
-->

Antes de começar, o setup do ambiente é o [Lab 01 - Setup e configuração de ambiente](../01-create-codespaces/README.md). O [Lab 02 - Do modelo ao sistema de Machine Learning](../02-ml-system/README.md) é a referência conceitual deste laboratório (o mesmo padrão de dois estágios, o mesmo jeito de ler o artefato pela API), mas este lab **não depende de nenhum arquivo runtime do Lab 02**: ele gera o próprio treino do zero.

Todos os comandos rodam **no terminal do mesmo Codespaces** que você já usa desde o Lab 01. A partir do Passo 12.1 você também vai ler um painel do CloudWatch no navegador — mas ele é criado pelo Terraform junto com o resto, então nada neste laboratório é provisionado clicando no console.

> [!WARNING]
> **Pré-requisitos. Confira estes quatro itens antes de continuar:**
>
> - [ ] Lab 01 e Lab 02 concluídos (mesmo Codespaces, mesma conta AWS).
> - [ ] Sessão do AWS Academy Learner Lab **iniciada**.
> - [ ] Credenciais do Academy copiadas para `~/.aws/credentials`. Elas expiram a cada 4 horas.
> - [ ] Crédito disponível no Learner Lab (o risco real é esquecer o endpoint real-time ligado).
>
> **Tempo estimado: 75 a 95 minutos.**

No Lab 02 você entregou **um** jeito de servir o modelo: um endpoint sempre ligado. Aqui você aprende que "sempre ligado" é uma escolha, não a única opção, e a escolha certa depende do contrato do workload, não do modelo em si.

## Principais pontos de aprendizagem

- Por que não existe um padrão de serving universalmente melhor: o que muda é SLA, volume, concorrência, payload e tolerância a espera.
- A diferença entre computação **persistente** (real-time, async com capacidade > 0) e **sob demanda** (serverless, batch).
- Por que scaling automático e demonstração determinística de elasticidade são coisas diferentes.
- Por que o mesmo `model.tar.gz` pode sustentar quatro contratos de consumo sem ser copiado quatro vezes.
- A diferença entre latência (p50/p95/p99) e throughput (requests/segundo), e por que nenhuma das duas sozinha decide uma arquitetura.

## O que você terá ao final

Um único modelo servido por três endpoints simultâneos (real-time, serverless, async) mais um job de batch transform efêmero, com autoscaling real configurado e uma demonstração controlada de elasticidade 1→2→1 provada por API. Um painel do CloudWatch, provisionado junto da infraestrutura, mostra os quatro padrões lado a lado enquanto você executa. Um dossiê em `artifacts/evidence/` documenta cada afirmação, e a conta termina limpa, comprovada por varredura de API.

### Arquitetura

![Arquitetura do Lab 03: Terraform provisiona o bootstrap de treino, o artefato único alimenta Real-Time, Serverless e Async Endpoints mais um Batch Transform Job, com Application Auto Scaling e CloudWatch monitorando o Real-Time e o Async.](diagramas/arquitetura.png)

Um único `model.tar.gz` sai do training job de bootstrap e alimenta quatro formas de consumo: o Real-Time Endpoint (instância sempre ligada), o Serverless Endpoint (capacidade gerenciada pela AWS), o Async Endpoint (fila via S3, pode ir a zero) e um Batch Transform Job (computação efêmora, sem endpoint). Application Auto Scaling e CloudWatch (linhas tracejadas) cuidam da elasticidade do Real-Time e do Async: são observabilidade, não o caminho do dado. Fonte editável em [`diagramas/arquitetura.excalidraw`](diagramas/arquitetura.excalidraw).

> [!TIP]
> Os blocos **💡 Clique para entender** são aprofundamentos opcionais. Os blocos **⚠ Se der erro** aparecem logo depois do passo que pode falhar.

## Mapa do lab

| Parte | O que você faz | Passos | Tempo |
|---|---|---|---|
| [Parte 1 - Ambiente e portão de entrada](#parte-1---ambiente-e-portão-de-entrada) | Reabre o Codespaces, instala o que é específico deste lab, roda o portão de entrada. | [1](#passo-1) · [2](#passo-2) · [3](#passo-3) · [4](#passo-4) · [5](#passo-5) | ~10 min |
| [Parte 2 - Um workload comum](#parte-2---um-workload-comum) | Gera o dataset e identifica os quatro contratos de workload. | [6](#passo-6) · [7](#passo-7) · [8](#passo-8) | ~10 min |
| [Parte 3 - Um modelo, três formas de serving](#parte-3---um-modelo-três-formas-de-serving) | Sobe o bootstrap de treino e os três endpoints com um único comando, e abre o painel do lab. | [9](#passo-9) · [10](#passo-10) · [11](#passo-11) · [12](#passo-12) · [12.1](#passo-12-1) | ~18 min |
| [Parte 4 - Síncrono persistente vs serverless](#parte-4---síncrono-persistente-vs-serverless) | Compara latência e comportamento entre real-time e serverless, no terminal e no painel. | [13](#passo-13) · [13.1](#passo-13-1) · [14](#passo-14) | ~13 min |
| [Parte 5 - Quando esperar é parte do contrato](#parte-5---quando-esperar-é-parte-do-contrato) | Roda async e batch, compara os dois e vê a fila sendo drenada. | [15](#passo-15) · [16](#passo-16) · [16.1](#passo-16-1) · [17](#passo-17) · [17.1](#passo-17-1) · [18](#passo-18) | ~20 min |
| [Parte 6 - Concorrência e elasticidade](#parte-6---concorrência-e-elasticidade) | Load test e demonstração controlada de scaling 1→2→1, com a curva desenhada. | [19](#passo-19) · [19.1](#passo-19-1) · [20](#passo-20) · [21](#passo-21) · [21.1](#passo-21-1) · [22](#passo-22) | ~20 min |
| [Parte 7 - Dossiê e decisão](#parte-7---dossiê-e-decisão) | Consolida a evidência e escreve a recomendação para Helena. | [23](#passo-23) · [24](#passo-24) | ~10 min |
| [Parte 8 - Encerramento obrigatório](#parte-8---encerramento-obrigatório) | Destrói tudo e prova por API que nada faturável sobrou. | [25](#passo-25) · [26](#passo-26) | ~10 min |

Travou em algum passo? Clique no número na tabela acima para pular direto para ele.

---

## Contexto

> Quinta-feira, 10h05. O endpoint demonstrado na aula anterior funciona. **Helena Marques, diretora de receita** da **Bora Fibra**, volta à sala com uma nova preocupação:
>
> > *"Agora eu consigo pedir o risco de churn de um cliente. Só que temos quatro situações diferentes: atendimento precisa de resposta na hora; o app recebe picos quando fecha a fatura; marketing quer pontuar a base toda de madrugada; e alguns arquivos grandes podem esperar. Eu não quero pagar infraestrutura 24 horas por dia para tudo. Como escolhemos o jeito certo de servir e como isso escala quando o tráfego muda?"*

Não existe serving universalmente melhor. O que muda é o **contrato do workload**: SLA, volume, concorrência, payload, tolerância a espera, frequência e custo de ociosidade.

### Pergunta-âncora do laboratório

> **Qual padrão de inferência atende este workload com o menor custo e complexidade sem violar o SLA que o negócio realmente precisa?**

Você vai responder essa pergunta em quatro marcos: antes do deploy (abstrata), depois de comparar real-time e serverless (evidência de latência/ociosidade), depois de async e batch (a dimensão fila/lote aparece), e depois do scaling (elasticidade e custo operacional entram na conta).

### Quatro workloads, quatro padrões

| Workload | SLA / comportamento | Padrão candidato | O que o lab prova |
|---|---|---|---|
| Atendimento humano | resposta síncrona, baixa latência, tráfego contínuo | Real-Time Endpoint | endpoint persistente e previsível |
| App após fechamento da fatura | rajadas curtas, longos períodos ociosos | Serverless Inference | paga pelo uso, tem comportamento de first-request |
| Importação de arquivo pesado | não bloqueia o cliente, aceita fila | Asynchronous Inference | request desacoplada via S3, pode escalar a zero |
| Campanha noturna | centenas/milhares de registros de uma vez | Batch Transform | computação efêmera, sem endpoint persistente |

### Por que esta arquitetura existe

| Problema de negócio | Responde bem | Responde mal | Quando acontece na vida real |
|---|---|---|---|
| Retenção precisa do risco durante a ligação | probabilidade **deste** cliente, agora, em milissegundos | pontuar os 180 mil clientes de uma vez (isso é lote, não endpoint) | atendimento, cobrança, antifraude |
| App consulta risco só depois do fechamento da fatura | paga só pelo uso, sem instância ociosa o resto do mês | tráfego constante e alta concorrência (cold behavior vira custo de UX) | picos previsíveis e esparsos |
| Um arquivo com 50 mil linhas chega para pontuar | desacopla o cliente da espera, processa quando dá | resposta que precisa ser síncrona | importação, processamento em segundo plano |
| Campanha pontua a base inteira à noite | computação efêmera, sem endpoint 24/7 | qualquer chamada individual síncrona | relatório periódico, score de carteira |

> [!CAUTION]
> **O único jeito de gastar de verdade neste laboratório é esquecer um endpoint ligado.**
>
> | Recurso | Quando cobra | Risco em aula |
> |---|---|---|
> | Training `ml.m5.large` | só durante o bootstrap | baixo, termina sozinho |
> | **Real-Time `ml.m5.large`** | **enquanto o endpoint existir** | **alto: é o vilão de ociosidade** |
> | Async `ml.m5.large` | enquanto capacidade > 0; pode ir a zero | médio |
> | Serverless | por invocação/duração | baixo quando ocioso |
> | Batch Transform `ml.m5.large` | só a duração do job | baixo, efêmero |
> | S3 | storage/request | desprezível neste volume |
>
> Ordem de grandeza: um `ml.m5.large` fica na casa de **US$ 0,1/h**; confira o [pricing atual do SageMaker](https://aws.amazon.com/sagemaker/pricing/). O Passo 25 destrói tudo, e o Passo 26 prova que foi destruído.

<details>
<summary><b>💡 Clique para entender: por que scaling automático e "prova de elasticidade em aula" são coisas diferentes</b></summary>
<blockquote>

O target tracking do Real-Time (`SageMakerVariantInvocationsPerInstance`) fica **de fato configurado** neste lab, não é decoração. Mas esperar o CloudWatch agregar métricas e a política reagir tornaria a aula dependente de janelas de tempo que variam a cada execução.

Por isso o Passo 21 (`make scale-demo`) não espera o tráfego real disparar a política: ele eleva o `MinCapacity`/`MaxCapacity` do scalable target diretamente via Application Auto Scaling, prova por `DescribeEndpoint` que a contagem de instâncias foi de 1 para 2, e depois restaura exatamente a configuração que o Terraform gerencia (`MinCapacity=1`, `MaxCapacity=2`). A política de target tracking continua lá, pronta para reagir a tráfego real fora da aula.

📚 Documentação oficial: [Automatic scaling for real-time endpoints](https://docs.aws.amazon.com/sagemaker/latest/dg/endpoint-auto-scaling.html) e [Configure a scaling policy](https://docs.aws.amazon.com/sagemaker/latest/dg/endpoint-auto-scaling-policy.html).

</blockquote>
</details>

---

## Parte 1 - Ambiente e portão de entrada

### Resultado esperado desta parte

`make doctor` respondendo `[PASS] verificação prévia`, com Terraform, Python, a conta do Learner Lab identificada, `us-east-1` confirmada e a `LabRole` encontrada.

<a id="passo-1"></a>

**1. Reabra o Codespaces da disciplina**

Você não cria Codespaces novo neste lab. Abra [github.com/codespaces](https://github.com/codespaces) e clique no ambiente que você já usa desde o Lab 01. Se estiver `Stopped`, o próprio clique o religa.

---

<a id="passo-2"></a>

**2. Entre na pasta deste laboratório**

```bash
cd /workspaces/FIAP-Cloud-Based-Machine-Learning
git pull origin master
cd 03-serving-and-scaling
```

---

<a id="passo-3"></a>

**3. Instale o que este laboratório precisa**

```bash
make setup
```

> Saída esperada (leva de 30 a 90 segundos):
> ```text
> ==> terraform 1.15.8 disponível
> ==> terraform : Terraform v1.15.8
> ==> python    : Python 3.x.x
> ==> pronto. Próximo passo: make doctor
> ```

<details>
<summary><b>⚠ Se der erro: <code>terraform: command not found</code></b></summary>
<blockquote>

O ambiente base do Codespaces já traz Terraform (ver [Lab 01](../01-create-codespaces/README.md)). Se este comando não encontrar o binário, abra um terminal novo e confira `terraform version` antes de repetir `make setup`.

</blockquote>
</details>

<details>
<summary><b>💡 Clique para entender: o que <code>make setup</code> faz de verdade</b></summary>
<blockquote>

`scripts/setup.sh` faz duas coisas, nesta ordem: confere se o `terraform` já está no `PATH` (se não estiver, para com uma mensagem clara em vez de tentar instalar algo); e cria (ou reaproveita, se já existir) o `.venv` deste lab e roda `pip install -r requirements.txt`, que trava boto3/botocore/numpy/scikit-learn/PyYAML nas versões exatas testadas. É idempotente: rodar de novo não reinstala o que já está na versão certa, só confirma.

</blockquote>
</details>

---

<a id="passo-4"></a>

**4. Conheça a superfície de comandos do laboratório**

```bash
make help
```

> Saída esperada:
> ```text
> Lab 03 - Serving and Scaling
>
>   help           Lista os alvos disponíveis
>   setup          Cria/atualiza o .venv do lab com as dependências fixadas
>   doctor         Confere versões das ferramentas e credenciais/região/role da AWS
>   data           Gera o dataset determinístico
>   validate-data  Roda o contrato de dados executável e imprime os hashes exatos
>   fmt            Formata o Terraform (confere no CI, reescreve localmente)
>   validate       terraform init + validate
>   plan           Planeja o estágio atual
>   apply          Provisiona storage + bootstrap de treino, portão, e então 3 endpoints + autoscaling
>   status         Descreve endpoints, configs e scalable targets em JSON
>   dashboard      Imprime os links dos dois painéis do CloudWatch (o do lab e o de observação ao vivo)
>   resumo         Preenche a tabela de evidências do DECISION.md e imprime os números medidos
>   compare        Smoke + latência, real-time vs serverless (DURACAO=180 mantém tráfego por 3 min)
>   async          Sobe o payload para o S3, InvokeEndpointAsync, espera e valida a saída
>   batch          CreateTransformJob para as 600 linhas de teste, espera e valida as 600 saídas
>   load           Carga no real-time com concorrência 1/4/8 (DURACAO=180 dá um minuto a cada nível)
>   scale-demo     Prova 1->2->1 instâncias e restaura (DURACAO=120 segura o degrau e gera tráfego de fundo)
>   evidence       Consolida os resultados conferíveis em artifacts/evidence/
>   destroy        Destrói todos os recursos gerenciados
>   verify-clean   Prova por consulta direta à API que não sobrou nada cobrando com o prefixo deste lab
>   e2e            Ciclo completo com limpeza à prova de falha (KEEP_RESOURCES=1 pula o destroy)
>   clean          Remove artefatos locais gerados (nunca toca na AWS)
>
>   Ciclo completo:  make e2e
>   Manter recursos: make e2e KEEP_RESOURCES=1   (você precisa rodar make destroy depois)
> ```

São 22 comandos, e é a lista inteira do laboratório.

<details>
<summary><b>💡 Clique para entender: o que cada comando faz de verdade</b></summary>
<blockquote>

| Comando | O que ele roda | AWS/custo | Por que existe |
|---|---|---|---|
| `make help` | lista os targets deste Makefile | não | ponto de entrada |
| `make setup` | cria/atualiza `.venv` com as versões pinadas | não | dependência de todo o resto |
| `make doctor` | versões + `sts:GetCallerIdentity` + `LabRole` + leituras de SageMaker/S3/CloudWatch/Application Auto Scaling | não, só leitura | portão de entrada: barra o resto se o ambiente estiver errado |
| `make data` | gera os 6 arquivos determinísticos em `artifacts/data/` | não | único jeito de gerar dataset neste lab |
| `make validate-data` | contrato executável: contagens, colunas, rótulo binário, sem vazamento, hashes | não | falha barata antes de qualquer custo |
| `make fmt` | `terraform fmt -recursive` | não | higiene de código |
| `make validate` | `terraform init` + `fmt -check` + `validate` | não | sintaxe/providers antes de planejar |
| `make plan` | `validate-data` + `validate`, depois `terraform plan` | não cria recurso | mostra o que vai mudar |
| `make apply` | stage 1 (S3 + training bootstrap) → portão (`DescribeTrainingJob` + `HeadObject`) → stage 2 (model + 3 endpoint configs/endpoints + autoscaling) | **sim** | o comando que sobe tudo, em um passo só |
| `make status` | `DescribeEndpoint`/`DescribeEndpointConfig`/`DescribeScalableTargets` dos três modos | não, só leitura | inventário rápido do que está no ar |
| `make dashboard` | `GetDashboard` nos dois painéis criados pelo estágio 2 e imprime os dois links | não, só leitura | os painéis são a superfície visual do lab; você não precisa achar o nome deles no console |
| `make resumo` | lê os JSON de `artifacts/evidence/` que já existem, imprime os números e regrava a tabela de evidências dentro do `DECISION.md` | não, nem toca a AWS | o documento fica sendo só a sua decisão; os dados entram sozinhos |
| `make compare` | 1 chamada + 20 chamadas warm, real-time e serverless, com o mesmo payload fixo. Com `DURACAO=180`, alterna chamadas nos dois endpoints por 3 minutos em vez da rajada curta | invocações pequenas | mede latência e prova que as predictions batem; a duração existe para o painel ter linha em vez de ponto |
| `make async` | sobe payload no S3, `InvokeEndpointAsync`, espera o output aparecer no S3 | sim, pequeno | prova o desacoplamento request/resposta |
| `make batch` | `CreateTransformJob` via Boto3 nos 600 registros de teste | sim, efêmero | prova computação sem endpoint persistente |
| `make load` | matriz de concorrência 1/4/8 no real-time, calcula p50/p95/p99/RPS. Com `DURACAO=180`, cada nível ocupa um minuto em vez de um número fixo de requisições | invocações | mede throughput sob pressão; a duração desenha três degraus no painel e provoca o scale-out de verdade |
| `make scale-demo` | normaliza para 1 instância se preciso, eleva o `MinCapacity`, prova 1→2 por `DescribeEndpoint`, restaura. Com `DURACAO=120`, segura o degrau por dois minutos e mantém tráfego de fundo | sim, enquanto houver 2 instâncias | demonstração controlada de elasticidade; a duração é o que torna o degrau e a distribuição visíveis |
| `make evidence` | consulta as APIs de novo e consolida tudo em `artifacts/evidence/` | não, só leitura | dossiê rastreável |
| `make destroy` | `terraform destroy` | encerra o custo | desliga tudo que foi criado |
| `make verify-clean` | pergunta direto às APIs (sem olhar o state) se sobrou algo com o prefixo do lab | não | não confia no que o Terraform *acha* que destruiu |
| `make e2e` | encadeia tudo com `trap` de limpeza garantida | **sim, ciclo completo** | validação automatizada do lab inteiro |
| `make clean` | apaga `artifacts/` e caches locais | não, nunca toca a AWS | recomeçar do zero na sua máquina |

`apply` já roda `validate-data` e `validate` sozinho; `plan` já roda os dois também. Você não precisa encadear nada manualmente.

</blockquote>
</details>

---

<a id="passo-5"></a>

**5. Rode o portão de entrada**

```bash
make doctor
```

> Saída esperada (o número da conta é o da sua conta):
> ```text
> Verificação prévia da AWS
>   conta            : 123456789012
>   chamador         : arn:aws:sts::1234****9012:assumed-role/voclabs/user1234567=
>   região           : us-east-1 (exigida us-east-1)
>   role de execução : arn:aws:iam::1234****9012:role/LabRole
>   bucket do lab    : prb-cloud-ml-lab2-123456789012-us-east-1
>   sagemaker_reachable             : ok
>   s3_reachable                    : ok
>   cloudwatch_reachable            : ok
>   application_autoscaling_reachable: ok
>   este lab nunca imprime credencial
> [PASS] verificação prévia
> ```

![](img/01-make-doctor.png)

<details>
<summary><b>⚠ Se der erro: <code>ExpiredToken</code> ou credencial rejeitada</b></summary>
<blockquote>

A credencial do Academy venceu. Copie um novo bloco de credenciais do Learner Lab para `~/.aws/credentials` e rode `make doctor` de novo.

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: <code>NoSuchEntity</code> na <code>LabRole</code></b></summary>
<blockquote>

A credencial que você colou provavelmente não é a do Learner Lab desta disciplina. Confirme a conta com `aws sts get-caller-identity --query Account --output text` e compare com o AWS Academy.

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: <code>session region is X but this lab requires 'us-east-1'</code></b></summary>
<blockquote>

O seu perfil AWS está apontando para outra região. Force a região correta e rode de novo:

```bash
aws configure set region us-east-1
make doctor
```

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: <code>AccessDenied</code> ao criar recurso do SageMaker</b></summary>
<blockquote>

Este lab só está autorizado a usar a `LabRole` pré-existente do Academy, nunca cria role própria. Se um recurso reportar `AccessDenied`, confira se o Terraform está mesmo assumindo `LabRole` (`terraform output execution_role_arn`) e não uma credencial pessoal por engano.

</blockquote>
</details>

### Checkpoint

- [x] `make doctor` termina com `[PASS] verificação prévia`.
- [x] Os quatro serviços (SageMaker, S3, CloudWatch, Application Auto Scaling) respondem `ok`.

Nenhum recurso foi criado na AWS até aqui.

---

## Parte 2 - Um workload comum

### Resultado esperado desta parte

Seis arquivos em `artifacts/data/`, contrato de dados aprovando 100% dos checks, e os quatro contratos de workload identificados em `config/lab.yaml`.

<a id="passo-6"></a>

**6. Gere o dataset**

```bash
make data
```

> Saída esperada:
> ```text
> [data] semente=42 n_samples=4000 destino=/workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling/artifacts/data
> [data] train      2800 linhas  prevalência 0.3450
> [data] validation  600 linhas  prevalência 0.3450
> [data] test        600 linhas  prevalência 0.3450
> [data] escrito em /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling/artifacts/data
> ```

![](img/02-make-data.png)

<details>
<summary><b>💡 Clique para entender: por que este dataset é diferente do Lab 02</b></summary>
<blockquote>

O Lab 02 gerou um dataset com características nomeadas (`tenure_months`, `monthly_charges`...) para ensinar o vocabulário de churn. Este lab usa `sklearn.datasets.make_classification` com dez características genéricas (`f0`...`f9`) e semente fixa (`seed=42`): o foco pedagógico aqui é **como servir**, não engenharia de características, e um gerador mais simples deixa o lab tecnicamente independente do Lab 02.

A prevalência de churn (~34,5%) e a separação das classes foram calibradas para o XGBoost aprender algo real sem ser trivial, do mesmo jeito que no Lab 02.

</blockquote>
</details>

<details>
<summary><b>💡 Clique para entender: como <code>make data</code> gera e divide as 4.000 linhas</b></summary>
<blockquote>

`make_classification` cria as 4.000 linhas de uma vez (`weights=[0.66, 0.34]` fixa a prevalência, `class_sep=1.6` calibra a dificuldade). Depois, dois `train_test_split` estratificados em sequência: o primeiro separa 2.800 linhas de treino do restante; o segundo divide o restante em 600 de validação e 600 de teste. Estratificado significa que a proporção de churn é preservada nas três partes, não só na base inteira.

A partir das mesmas 600 linhas de teste, o script grava **três arquivos diferentes**, cada um no formato que o consumidor exige: `test_labeled.csv` (com `id` e rótulo, para você auditar), `test_features.csv` (sem rótulo, para `compare`/`load`) e `batch_input.csv` (sem rótulo, para o Passo 17): os dois últimos têm o mesmo conteúdo e por isso o mesmo hash SHA-256 no Passo 7. `async_payload.csv` é só as primeiras 50 linhas de `test_features.csv`.

📚 Documentação oficial: [`sklearn.datasets.make_classification`](https://scikit-learn.org/stable/modules/generated/sklearn.datasets.make_classification.html) e [`train_test_split` (parâmetro `stratify`)](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.train_test_split.html).

</blockquote>
</details>

---

<a id="passo-7"></a>

**7. Rode o contrato de dados**

```bash
make validate-data
```

> Saída esperada (os hashes abaixo são os publicados por esta execução; os seus devem ser idênticos porque a semente é fixa):
> ```text
>   [PASS] files.present
>   [PASS] row_count.train
>   [PASS] row_count.validation
>   [PASS] row_count.test_features
>   [PASS] row_count.batch_input
>   [PASS] column_count.train
>   [PASS] column_count.validation
>   [PASS] column_count.test_features
>   [PASS] label.binary
>   [PASS] values.no_nan_or_inf
>   [PASS] test_features.matches_labeled_features_no_leak
>   [PASS] feature_order.matches_manifest
>   [PASS] manifest.sha256_matches_files
> [PASS] contrato de dados
> ```
>
> SHA-256 publicados (conferidos nesta execução):
> ```text
> train.csv          e7ec518260489fb762e760be99111e6a7bd6eaed7a9b8ade07b7ffc8da1813a8
> validation.csv      9eca965e66a5c9a5b9919a4b220465e914a9a91c5a4f21e59f9063206fc9b6c4
> test_features.csv  9e6b99efff09146ab06b01e9deb13253d0638bdd26e792ca2e0c02080be66724
> batch_input.csv    9e6b99efff09146ab06b01e9deb13253d0638bdd26e792ca2e0c02080be66724
> async_payload.csv  ca70dcbed6bcef920906ccbe1b4e364c39c5c59702f214d892c21778e5ddf49d
> ```

![](img/03-contrato-13-checks.png)

`test_features.csv` e `batch_input.csv` têm o mesmo hash de propósito: são o mesmo conjunto de 600 linhas de teste, usado por dois canais diferentes (invocação direta vs. transform job).

<details>
<summary><b>💡 Clique para entender: como o contrato confere os 13 checks</b></summary>
<blockquote>

O contrato roda inteiro na sua máquina, sem custo. Três grupos de verificação: **contagem/formato** (número exato de linhas e colunas de cada arquivo, rótulo só com 0/1, nenhum `NaN`/`inf`); **integridade** (recalcula o SHA-256 de cada arquivo e compara com o que está gravado em `dataset_manifest.json`, gerado no Passo 6; se alguém editar um CSV à mão, falha aqui); e **vazamento de rótulo**, o mais importante: ele compara, valor a valor, as características de `test_labeled.csv` (que tem o rótulo) contra `test_features.csv` (que não tem) — se a ordem das colunas tivesse mudado ou o rótulo tivesse vazado para o arquivo de inferência, essa comparação reprovaria em vez de deixar passar um número plausível e errado.

</blockquote>
</details>

---

<a id="passo-8"></a>

**8. Identifique os quatro contratos de workload**

```bash
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling
sed -n '/^workloads:/,/^realtime:/p' config/lab.yaml
```

> Saída esperada (quatro blocos, um por workload, cada um apontando para um padrão):
> ```text
> workloads:
>   atendimento:
>     nome: "Atendimento humano"
>     padrao: realtime
>     ...
>   campanha_noturna:
>     nome: "Campanha noturna"
>     padrao: batch
>     ...
> ```

Guarde esses quatro nomes: eles reaparecem no `DECISION.md` no Passo 24.

### Checkpoint

- [x] `artifacts/data/` tem os seis arquivos.
- [x] `[PASS] contrato de dados` com os 13 checks aprovando.
- [x] Você identificou os quatro `padrao:` em `config/lab.yaml`.

Continua sem nenhum recurso criado na AWS.

---

## Parte 3 - Um modelo, três formas de serving

### Resultado esperado desta parte

Um bootstrap de treino concluído, um `model.tar.gz` real no S3, e três endpoints (`real-time`, `serverless`, `async`) no estado `InService`, tudo de um único `make apply`.

> [!CAUTION]
> **A partir do Passo 10 você começa a gastar de verdade.** O bootstrap de treino custa uma fração de centavo e termina sozinho. O real-time e o async cobram enquanto existirem. Se a aula terminar antes da Parte 8, rode `make destroy` de qualquer forma; você recria tudo depois com um comando.

<a id="passo-9"></a>

**9. Leia o plano antes de criar nada**

```bash
make plan
```

> Saída esperada (a linha que interessa está no fim):
> ```text
> Plan: 9 to add, 0 to change, 0 to destroy.
> ```

Nove recursos no primeiro estágio, nenhum deles um endpoint: o bucket e suas quatro configurações de segurança, dois objetos de metadados, dois objetos de dados e o training job.

<details>
<summary><b>💡 Clique para entender: como <code>make plan</code> descobre o que vai mudar</b></summary>
<blockquote>

`plan` primeiro roda `validate-data` e `validate` (que fazem `terraform init` + `fmt -check` + `validate`), depois `terraform plan -input=false`. Nenhuma chamada de criação acontece: o Terraform lê o `.tf` do repositório, compara com o que está gravado no `terraform.tfstate` local e, para cada recurso, consulta a API da AWS para confirmar que a realidade bate com o state. A diferença entre os três (código, state, realidade) é o que aparece como `+`/`-`/`~` na saída. Como a variável `deploy_serving` está em `false` por padrão (nenhum handoff ainda existe), o plano nem tenta descrever o Model ou os Endpoints — eles só entram no gráfico de recursos quando essa variável vira `true`, no estágio 2 do `make apply`.

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: <code>plan</code> quer recriar o serving a partir de um <code>artifact.auto.tfvars.json</code> velho</b></summary>
<blockquote>

Esse arquivo é gerado pelo portão dentro de `make apply` e referencia o artefato de um ciclo anterior. Se um `make apply` foi interrompido, ele pode ter sobrado apontando para um artefato que já não existe. Apague-o antes de repetir:

```bash
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling
rm -f terraform/artifact.auto.tfvars.json
make apply
```

`make apply` já apaga esse arquivo no início do estágio 1, então isso só importa se você rodou `terraform apply` manualmente por fora do `make`.

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: <code>Unsupported Terraform Core version</code></b></summary>
<blockquote>

```text
Error: Unsupported Terraform Core version

  on versions.tf line 5, in terraform:
   5:   required_version = "= 1.15.8"
```

Seu Codespaces tem uma versão de Terraform diferente da que este laboratório fixa. Acontece com quem criou o ambiente em aulas passadas e não fez rebuild: o `.devcontainer/` instala a **1.15.8**, mas um Codespaces antigo pode ter ficado numa anterior.

Confira o que você tem e instale a versão da disciplina — o `setup.sh` do Lab 02 baixa o binário oficial e confere o SHA-256 antes de instalar:

```bash
terraform version
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/02-ml-system && make setup
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling && make plan
```

O pino é exato (`=`, não `~>`) de propósito: numa sala em que cada aluno resolve uma versão diferente, a aula vira depuração de Terraform em vez de sistemas de ML.

</blockquote>
</details>

---

<a id="passo-10"></a>

**10. Aplique: bootstrap de treino, portão, três endpoints**

```bash
make apply
```

> [!IMPORTANT]
> Este comando leva vários minutos e **não deve ser interrompido**. Ele faz o bootstrap de treino, espera o portão (`DescribeTrainingJob` + `HeadObject`) e sobe os três endpoints em sequência. É o único comando de toda a Parte 3.

> Saída esperada (resumida; a sua vai ter um sufixo diferente de `dc8799d3`, e os tempos variam por execução):
> ```text
> == estágio 1/2: storage e bootstrap de treino ==
> ...
> aws_sagemaker_training_job.churn: Creation complete after 1s
> Apply complete! Resources: 9 added, 0 changed, 0 destroyed.
> == portão: esperar o training job e provar que o artefato existe ==
> [training] prb-cloud-ml-lab2-train-dc8799d3: Completed / Completed
> [wait] prb-cloud-ml-lab2-train-dc8799d3: Completed em 135s cobrados
> [wait] artefato comprovado via HeadObject: s3://.../model.tar.gz (21717 bytes)
> == estágio 2/2: model, 3 endpoint configs/endpoints, autoscaling ==
> ...
> aws_sagemaker_endpoint.serverless[0]: Creation complete after 3m1s
> aws_sagemaker_endpoint.realtime[0]: Creation complete after 3m29s
> aws_sagemaker_endpoint.async[0]: Creation complete after 3m33s
> Apply complete! Resources: 13 added, 0 changed, 0 destroyed.
> ```

![](img/04-make-apply.png)
![](img/04-make-apply2.png)

Repare que o job de treino é criado em 1 segundo, mas o treino em si não termina em 1 segundo: o Terraform submete o job, e é o portão (`wait-training` dentro de `make apply`) que espera o resultado real e prova o artefato antes do segundo estágio.

<details>
<summary><b>💡 Clique para entender: a mecânica exata do handoff entre os dois estágios</b></summary>
<blockquote>

`make apply` não é um só `terraform apply`, são dois, com um script Python no meio, tudo dentro do mesmo comando:

1. `rm -f terraform/artifact.auto.tfvars.json`: apaga qualquer handoff de um ciclo anterior.
2. `terraform apply -var deploy_serving=false`: estágio 1. Cria o bucket, os objetos de dados e o `aws_sagemaker_training_job`. Esse recurso retorna assim que a AWS aceita o job (`InProgress`), não quando ele termina.
3. `scripts/lab.py wait-training`: faz `DescribeTrainingJob` em loop (a cada 20s) até o status virar `Completed`. Só então lê `ModelArtifacts.S3ModelArtifacts` da própria resposta da API, faz um `HeadObject` nesse URI exato para confirmar que o arquivo existe de verdade, e escreve `terraform/artifact.auto.tfvars.json` com `deploy_serving=true` e a URI provada.
4. `terraform apply` (sem `-var`, desta vez lendo o `.auto.tfvars.json` do passo 3): estágio 2. Cria o `Model` (apontando para a URI provada), as três `EndpointConfiguration`, os três `Endpoint` e toda a Application Auto Scaling — o Terraform sobe os três endpoints em paralelo, por isso os tempos de criação aparecem intercalados na saída.

Nunca existe um momento em que o Terraform "adivinha" o caminho do artefato: cada estágio só lê o que o estágio anterior provou.

📚 Documentação oficial: [Train a Model with Amazon SageMaker](https://docs.aws.amazon.com/sagemaker/latest/dg/how-it-works-training.html) e a referência da API [`DescribeTrainingJob`](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_DescribeTrainingJob.html) (é o campo `ModelArtifacts.S3ModelArtifacts` dessa resposta que o Passo 10 lê).

</blockquote>
</details>

<details>
<summary><b>💡 Clique para entender: por que um único Model alimenta três endpoints</b></summary>
<blockquote>

O `model_artifact_uri` vem de `DescribeTrainingJob`, nunca montado por convenção de pasta. A partir dele, o Terraform cria **um** recurso `aws_sagemaker_model`, e as três `EndpointConfiguration` (real-time, serverless, async) todas apontam para esse mesmo Model, cada uma descrevendo apenas *como* consumir o artefato, não uma cópia dele.

É por isso que a pergunta da Helena ("um modelo, quatro formas de consumir") tem uma resposta arquitetural exata: o artefato não muda entre os modos, só a receita de capacidade muda.

</blockquote>
</details>

<details>
<summary><b>💡 Clique para entender: por que o bucket aparece como <code>terraform_data.bucket</code></b></summary>
<blockquote>

O recurso natural seria `aws_s3_bucket`, e não é ele que está no código — pelo mesmo motivo do Lab 1.

Depois do `CreateBucket`, o provider **lê de volta** cerca de quinze sub-configurações do bucket para preencher o estado. Uma delas é `s3:GetBucketObjectLockConfiguration`, negada por Service Control Policy na organização do AWS Academy. O bucket é criado e o `apply` falha na leitura seguinte — e não há `lifecycle`, versão de provider ou `-refresh=false` que escape, porque essa leitura acontece **dentro** da criação.

No lugar dele, `terraform_data` (recurso nativo, sem provider) com um `local-exec` que cria o bucket pela CLI. O bucket continua sendo do Terraform: o nome vive no estado, os objetos de dados continuam esperando por ele no grafo de dependências, e `terraform destroy` o remove com `aws s3 rb --force` — o que importa aqui, porque `make batch` e o endpoint assíncrono escrevem objetos que o Terraform não gerencia. O preço é perder detecção de desvio (*drift*) nesse recurso.

📚 Documentação oficial: [`terraform_data`](https://developer.hashicorp.com/terraform/language/resources/terraform-data) e [Service Control Policies](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html).

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: <code>AccessDenied</code> em <code>s3:GetBucketObjectLockConfiguration</code></b></summary>
<blockquote>

A mensagem termina em `with an explicit deny in a service control policy` e cita `aws_s3_bucket` — recurso que este laboratório não declara mais. Logo, a origem é o **estado**: sobrou a entrada de uma execução antiga, e o Terraform repete a leitura negada em todo `plan`. Duas pistas confirmam: o plano é impresso normalmente antes do erro, e o bloco do erro não traz a linha `with <recurso>,`.

Remova só a entrada do estado — nada é apagado na AWS, e o bucket existente é reaproveitado no próximo `apply`:

```bash
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling
terraform -chdir=terraform state list | grep aws_s3_bucket
terraform -chdir=terraform state rm aws_s3_bucket.lab
make apply
```

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: <code>ExpiredToken</code> no meio do apply</b></summary>
<blockquote>

A credencial venceu durante a execução. Renove-a em `~/.aws/credentials` e rode `make apply` de novo: o Terraform continua de onde parou, sem duplicar recurso.

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: <code>ResourceLimitExceeded</code> ao subir um endpoint</b></summary>
<blockquote>

O Learner Lab às vezes tem capacidade limitada de `ml.m5.large` numa região/AZ específica. Espere 1-2 minutos e rode `make apply` de novo: o Terraform só recria o que faltou. Se persistir, troque `instance_type` em `terraform/variables.tf` para `ml.c5.large` (também permitido no Academy) e rode `make apply` de novo.

</blockquote>
</details>

<details>
<summary><b>⚠ Se o endpoint demorar mais do que da última vez</b></summary>
<blockquote>

O tempo de criação de endpoint varia por execução, não é uma banda fixa. O real-time e o async envolvem provisionar instância; o serverless não. Se um deles passar de 10 minutos, confira o status pelo console do SageMaker ou por `aws sagemaker describe-endpoint --endpoint-name <nome>`.

</blockquote>
</details>

---

<a id="passo-11"></a>

**11. Confira os três endpoints e as políticas de scaling**

```bash
make status
```

> Saída esperada (formato JSON; os três `status` devem ser `InService`):
> ```json
> {
>   "endpoints": {
>     "realtime": {"exists": true, "status": "InService", "current_instance_count": 1},
>     "serverless": {"exists": true, "status": "InService", "current_instance_count": 0},
>     "async": {"exists": true, "status": "InService", "current_instance_count": 1}
>   },
>   "scaling": {
>     "realtime": {"min_capacity": 1, "max_capacity": 2, "policy_names": ["prb-cloud-ml-lab2-rt-target"]},
>     "async": {"min_capacity": 0, "max_capacity": 1, "policy_names": ["prb-cloud-ml-lab2-async-target", "prb-cloud-ml-lab2-async-target-from-zero"]}
>   }
> }
> ```

`current_instance_count: 0` no serverless não é erro: esse modo não mantém instância provisionada entre chamadas, é exatamente o ponto do Passo 13.

<details>
<summary><b>💡 Clique para entender: de onde <code>make status</code> tira cada valor</b></summary>
<blockquote>

Nenhum valor vem do Terraform ou de arquivo local. Para cada um dos três endpoints, o comando chama `DescribeEndpoint` (status e `CurrentInstanceCount`); para o real-time e o async, chama também `DescribeScalableTargets` e `DescribeScalingPolicies` do Application Auto Scaling, filtrando pelo `resource_id` daquele endpoint (`endpoint/<nome>/variant/AllTraffic`). É só leitura: nada aqui cria, altera ou destrói recurso, por isso pode ser rodado quantas vezes quiser sem custo nem risco.

📚 Documentação oficial: referência da API [`DescribeEndpoint`](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_DescribeEndpoint.html) e [`DescribeScalableTargets`](https://docs.aws.amazon.com/autoscaling/application/APIReference/API_DescribeScalableTargets.html).

</blockquote>
</details>

---

<a id="passo-12"></a>

**12. Confirme opcionalmente no console (a prova oficial é a API)**

```bash
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling
JSON=$(make status)
echo "$JSON" | python3 -m json.tool 2>/dev/null | head -20
```
![](img/05-terminal-endpoints.png)

Se preferir ver com os próprios olhos, abra o console do SageMaker em [Endpoints](https://us-east-1.console.aws.amazon.com/sagemaker/home?region=us-east-1#/endpoints) — você deve ver três endpoints com o prefixo `prb-cloud-ml-lab2`, todos `InService`. A prova que o lab usa para seguir em frente continua sendo a saída do Passo 11.


![](img/05-console-endpoints.png)

---

<a id="passo-12-1"></a>

**12.1. Abra o painel do laboratório e deixe a aba aberta**

```bash
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling
make dashboard
```

> Saída esperada (os links são os seus; os nomes dos painéis são iguais para todo mundo):
> ```text
>   Painel do lab       : prb-cloud-ml-lab2-serving-8f7c85b8 (11 widgets, janela de 1 hora)
>   Painel ao vivo      : prb-cloud-ml-lab2-serving-ao-vivo-8f7c85b8 (4 widgets, janela de 5 minutos)
>
>   Deixe o painel do lab aberto do começo ao fim. Ele atualiza sozinho
>   conforme novas métricas chegam (granularidade de 60 s).
>
>   O painel ao vivo é para assistir `make compare DURACAO=180`: já abre nos
>   últimos 5 minutos, e você ajusta o intervalo de atualização para 10 s no
>   seletor do canto superior direito do console.
>
> https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards/dashboard/prb-cloud-ml-lab2-serving-8f7c85b8
> https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards/dashboard/prb-cloud-ml-lab2-serving-ao-vivo-8f7c85b8
> ```

O sufixo do nome muda a cada ciclo de vida, então **o seu link não é igual ao do exemplo** — use sempre o que o comando imprimiu. São **dois painéis**, e eles têm usos diferentes. O primeiro link é o painel do laboratório: onze widgets, janela de uma hora, e é o que você deixa aberto do começo ao fim. O segundo é o painel de observação ao vivo, com três gráficos e janela de cinco minutos, usado só na Parte 4, para assistir a comparação acontecendo. Abra o primeiro agora.

O `make apply` criou os dois junto com os endpoints. **Deixe a aba do painel do laboratório aberta**: as linhas dele acompanham as Partes 4, 5 e 6, e é nele que você vai comparar os padrões de serving em vez de somar números de cabeça.

Agora ele está praticamente vazio, e isso é o comportamento correto: nenhuma chamada foi feita ainda, e métrica de endpoint só passa a existir depois que alguém invoca.

O painel tem quatro linhas, e você vai ler uma por Parte:

| Linha | Quando você lê | Gráficos |
|---|---|---|
| 1 | Parte 4, no Passo 13.1 | latência do modelo · overhead da plataforma |
| 2 | Parte 5, nos Passos 16.1 e 17.1 | fila do assíncrono · fila sem capacidade · máquina do batch |
| 3 | Parte 6, nos Passos 19.1 e 21.1 | contagem de instâncias · carga total e por instância |
| 4 | quando quiser, vale para o lab inteiro | chamadas que falharam · teto de concorrência do serverless · CPU das instâncias |

A linha 4 é a de saúde e não tem passo próprio: ela existe para você conferir, em qualquer momento, que nada está falhando por trás dos números que está lendo. Se o widget "Alguma chamada falhou?" sair de zero em qualquer ponto do laboratório, pare e investigue antes de seguir — os números das outras linhas passam a não significar o que você acha.

![](img/painel-vazio.png)

<details>
<summary><b>💡 Clique para entender: o que <code>make dashboard</code> faz por baixo dos panos</b></summary>
<blockquote>

O comando lê `dashboard_name` e `dashboard_url` dos outputs do Terraform, chama `GetDashboard` nesse nome e conta os widgets do corpo que voltou, antes de imprimir o link. Essa chamada existe para o comando não te entregar uma URL que abre em 404: se o painel não subiu, você descobre aqui, não no navegador.

O painel é um recurso `aws_cloudwatch_dashboard` do Terraform, criado no estágio 2 junto dos endpoints e removido pelo `make destroy`. Ele não é montado à mão no console de propósito — assim todo mundo na sala lê exatamente o mesmo layout, e o `make verify-clean` consegue provar depois que ele não ficou para trás.

Uma característica do serviço que vale saber antes de estranhar: a granularidade mínima de métrica é de 60 segundos e o console reconsulta em intervalo próprio. O painel é **tempo quase real**, não tempo real.

📚 Documentação oficial: [Monitorar o Amazon SageMaker com o Amazon CloudWatch](https://docs.aws.amazon.com/sagemaker/latest/dg/monitoring-cloudwatch.html) — a tabela completa de métricas de endpoint, com a unidade de cada uma, inclusive as que este painel não usa.

</blockquote>
</details>

### Checkpoint

- [x] `Apply complete!` nos dois estágios.
- [x] `make status` mostra os três endpoints `InService`.
- [x] O scalable target do real-time mostra `min=1, max=2`; o do async mostra `min=0, max=1`.
- [x] `make dashboard` imprimiu o link e o painel abriu, com os gráficos ainda sem série.

**A partir daqui existem dois recursos cobrando por hora na sua conta (real-time e, enquanto tiver capacidade > 0, async).** Se precisar interromper a aula, pule para a [Parte 8](#parte-8---encerramento-obrigatório) e rode `make destroy`.

---

## Parte 4 - Síncrono persistente vs serverless

### Resultado esperado desta parte

Predictions equivalentes entre real-time e serverless, com o perfil de latência de cada um medido e registrado.

<a id="passo-13"></a>

**13. Compare real-time e serverless com a mesma lista de registros**

Antes de rodar, abra o **painel de observação ao vivo** numa aba nova. Ele é o segundo link que o Passo 12.1 imprimiu:

Se não tiver o link à mão, `make dashboard` imprime os dois de novo — e é o dele que vale, porque o nome do painel carrega o sufixo do seu ciclo de vida. Esse painel já abre mostrando os **últimos 5 minutos**. Falta um ajuste que só existe no console: no canto superior direito, **mude o intervalo de atualização para 10 segundos**. Feito isso, deixe a aba visível e rode:

```bash
make compare DURACAO=180
```

O comando mantém chamadas nos dois endpoints por três minutos, alternando entre eles, e você vê as duas séries se desenhando ao vivo.

> Saída esperada (contagens e tempos são medidos na sua execução; estes são de uma execução real):
> ```text
> [compare] mantendo tráfego nos dois endpoints por 180s (cada minuto vira um ponto no painel)
> [compare] faltam ~164s | chamadas: realtime=16 serverless=16
> [compare] faltam ~149s | chamadas: realtime=31 serverless=31
> [compare] realtime   chamadas=179 first=613.27ms warm_p50=449.194ms warm_p95=476.596ms
> [compare] serverless chamadas=179 first=6848.669ms warm_p50=471.603ms warm_p95=513.715ms
> [compare] predictions_match=True (tolerância 1e-06)
> ```

`predictions_match=True` é o que importa mais do que os milissegundos: prova que o mesmo artefato responde igual nos dois modos.

Repare no contraste entre as duas linhas de latência. A primeira chamada ao serverless custou **6,8 segundos** contra 613 ms no real-time; depois de aquecido, os dois andam juntos (471 ms contra 449 ms no p50). Esse é o comportamento de "primeira chamada" que a Helena precisa entender antes de escolher serverless para o app: a conta chega inteira só na primeira invocação depois de um período ocioso.

No painel ao vivo, a mesma execução desenhou quatro pontos por série (33, 64, 66 e 16 chamadas por minuto) — a linha que a rajada curta não produzia.

![](img/06-make-compare.png)

A imagem acima é de uma execução no **modo padrão**, sem `DURACAO`: serve para você reconhecer o formato das linhas de latência. No modo de três minutos as mesmas linhas aparecem no fim, precedidas pelos avisos de tempo restante.

> [!IMPORTANT]
> A métrica de endpoint do SageMaker tem granularidade mínima de **60 segundos**, e não existe resolução mais fina para métrica de serviço. Então três minutos de tráfego desenham cerca de **três pontos por série** — uma linha curta, não uma curva suave. Se quiser uma linha mais longa para projetar em aula, aumente a duração (`make compare DURACAO=300`).

<details>
<summary><b>💡 Clique para entender: por que existe o parâmetro <code>DURACAO</code></b></summary>
<blockquote>

Sem o parâmetro, o comando faz 1 chamada isolada e 20 chamadas seguidas, tudo em poucos segundos. Para medir latência isso é suficiente e é o modo padrão. Para **ver** a comparação, não é: as 21 chamadas caem todas dentro do mesmo intervalo de 60 segundos, então o gráfico mostra um ponto isolado por série. Tecnicamente correto, visualmente inútil.

Com `DURACAO`, o comando alterna chamadas entre os dois endpoints até o tempo acabar. A alternância não é detalhe: se o real-time recebesse os três minutos inteiros e só depois o serverless, as duas séries ficariam em janelas de tempo diferentes e o painel mostraria dois picos separados, não uma comparação.

O resultado gravado em `compare.json` ganha dois campos a mais nesse modo (`requests` por endpoint e `duration_s`), e o `success_rate` passa a ser medido de verdade em vez de fixo em 1.0 — numa janela de três minutos uma chamada pode falhar sem que isso invalide a medição.

Uma interação que vale conhecer antes de aumentar a duração: o ritmo é de aproximadamente uma chamada por segundo em cada endpoint, ou seja perto de 60 por minuto — exatamente o alvo da política de scaling do real-time. Numa execução real o pico bateu 66 chamadas num minuto e o endpoint **não** escalou, porque target tracking exige violação sustentada. Com uma duração bem maior, ele pode escalar para duas instâncias no meio da Parte 4, o que não quebra nada mas antecipa a história da Parte 6 (e cobra a segunda instância enquanto durar).

</blockquote>
</details>

<details>
<summary><b>💡 Clique para entender: como <code>make compare</code> mede e compara</b></summary>
<blockquote>

O comando pega uma lista fixa de 5 linhas de `test_features.csv` (as mesmas para os dois modos) e monta um único payload CSV com as 5. No modo padrão, para cada endpoint: faz **1 chamada isolada** e cronometra só ela (`first_ms`); depois faz **20 chamadas sequenciais** com o mesmo payload e guarda cada tempo de resposta, do qual calcula p50 e p95 por interpolação linear entre as amostras ordenadas. Ao final, compara as predictions da última chamada de cada modo, posição a posição, com tolerância `1e-6` — se qualquer par diferir mais que isso, `predictions_match` vira `False` e o comando termina com erro.

📚 Documentação oficial: [Serverless Inference](https://docs.aws.amazon.com/sagemaker/latest/dg/serverless-endpoints.html) e [Create a serverless inference endpoint configuration](https://docs.aws.amazon.com/sagemaker/latest/dg/serverless-endpoints-create-config.html) — a seção "Considerations" ali explica o comportamento de first-request que você acabou de medir.

</blockquote>
</details>

<details>
<summary><b>💡 Clique para entender: por que nenhum número de latência é critério de aprovação</b></summary>
<blockquote>

A latência de rede varia por execução, hora do dia e carga do Academy compartilhado. Publicar "o serverless deve responder em X ms" reprovaria uma execução correta que rodou em outro momento. O que o lab garante é a **comparação relativa** dentro da sua própria execução: first vs. warm, real-time vs. serverless, e isso é estável mesmo quando o valor absoluto não é.

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: <code>ModelError</code> ou latência do serverless muito alta na primeira chamada</b></summary>
<blockquote>

Isso é esperado, não um bug: um endpoint serverless que não recebe chamada por um tempo perde a capacidade provisionada, e a próxima chamada paga o custo de reprovisionar (cold start). É exatamente o "first-request behavior" que a arquitetura existe para expor.

</blockquote>
</details>

---

<a id="passo-13-1"></a>

**13.1. Leia a comparação de latência no painel**

Volte à aba do painel e recarregue. Os dois widgets da **linha 1** são desta parte:

| Widget | O que procurar |
|---|---|
| "Quem responde mais rápido, atendimento ou app?" | as duas séries quase coladas, ambas na casa de poucos milissegundos — é o mesmo artefato respondendo, então o modelo custa o mesmo nos dois |
| "Quanto custa não ter instância de pé?" | a série do serverless **várias vezes mais alta** que a do real-time, com separação visível sem esforço |
| "O app está perto do teto de concorrência? (%)" (linha 4) | um valor **baixo**, bem longe dos 100% — na casa de 20% numa execução real |

O terceiro widget responde uma pergunta que a linha 1 não responde: sobrou folga? Este laboratório configura o serverless com teto de **cinco execuções simultâneas**, e tanto o `compare` quanto o `load` chamam o endpoint uma de cada vez. Então o esperado é ocupação baixa: o serverless aqui nunca fica apertado, e a limitação que ele impõe ao app é a **primeira chamada**, não o teto de concorrência. É uma distinção que vale levar para o `DECISION.md`.

O contraste entre os dois widgets é a resposta para a Helena. O modelo custa o mesmo nos dois padrões; o que difere é o `OverheadLatency`, o tempo que o SageMaker gasta **fora** do modelo, e no serverless ele carrega a preparação do ambiente. Numa execução real medimos latência de modelo na casa de 4 a 6 ms nos dois, e overhead de aproximadamente 40 a 55 ms no real-time contra cerca de 380 ms no serverless — os valores variam por execução, mas a ordem de grandeza da diferença não.

Se as duas linhas de overhead estiverem quase coladas, seu serverless provavelmente continuava aquecido de uma execução anterior. Espere alguns minutos sem chamá-lo e rode `make compare` de novo.

**Você vai ver um pico isolado, não uma linha contínua.** O `make compare` dispara 21 chamadas em poucos segundos e para; só um intervalo de 60 segundos tem dado. Um gráfico com um ponto só não é defeito do painel, é o formato do tráfego que você acabou de gerar.

Se os dois gráficos estiverem completamente vazios, recarregue depois de um ou dois minutos antes de suspeitar de erro: a métrica do `compare` pode ainda não ter sido publicada.


![](img/painel-latencia.png)

---

<a id="passo-14"></a>

**14. Registre a decisão parcial no DECISION.md**

```bash
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling
make resumo
code DECISION.md
```

O `make resumo` faz duas coisas: imprime os números medidos no terminal e **escreve a tabela de evidências direto no `DECISION.md`**. Você abre o documento já com os dados no lugar, e o que resta a fazer nele é só a decisão.

Ele lê os JSON de `artifacts/evidence/` (que ficam fora do controle de versão, por isso aparecem esmaecidos no explorador) e regrava apenas o bloco entre os marcadores `inicio-evidencias` e `fim-evidencias`. O que você escreveu nas seções de recomendação nunca é tocado, e rodar de novo depois de cada medição só atualiza a tabela.

> Saída esperada (a parte que interessa neste passo; os números são da sua execução):
> ```text
> ATENDIMENTO HUMANO — Real-Time
>   Metade das chamadas respondeu em até 449 ms, e 95% em até 477 ms. A primeira chamada levou 613 ms.
>
> APP COM RAJADAS — Serverless
>   Depois de aquecido, metade em até 472 ms e 95% em até 514 ms — praticamente igual ao real-time.
>   Mas a primeira chamada depois de um tempo parado levou 6,8 segundos, cerca de 11 vezes o do real-time.
> ```

As mesmas frases vão para a tabela do `DECISION.md`, e o documento traz um bloco explicando o que são p50, p95 e milissegundo — você não precisa decorar nada para ler os números.

Com a tabela já preenchida, escreva nas seções **Atendimento** e **App com rajadas** da Recomendação: o padrão serve, qual custo de ociosidade você aceita, e qual limitação assume.

### Checkpoint

- [x] `predictions_match=True` no `compare.json`.
- [x] No painel, a linha 1 mostra o overhead do serverless acima do do real-time.
- [x] Você registrou latência real-time vs. serverless no `DECISION.md`.

---

## Parte 5 - Quando esperar é parte do contrato

### Resultado esperado desta parte

Um payload assíncrono processado com output provado no S3, e um batch transform job completo com 600 predictions.

<a id="passo-15"></a>

**15. Rode a inferência assíncrona**

```bash
make async
```

> Saída esperada (nomes e IDs mudam a cada execução):
> ```text
> [async] payload de 50 linhas enviado para s3://prb-cloud-ml-lab2-.../async/input/1787444194.csv
> [async] InferenceId=2300c4e2-c399-4fe4-8101-deafff68e80a output=s3://prb-cloud-ml-lab2-.../async/output/177e9993-d57f-4b68-8a02-d8689cd1cc93.out
> [async] input_count=50 output_count=50
> ```

![](img/07-make-async.png)

O `InferenceId` e o `output` location vêm da própria chamada `InvokeEndpointAsync`; o lab nunca monta esse caminho por convenção.

<details>
<summary><b>💡 Clique para entender: o que acontece entre o upload e o output aparecer</b></summary>
<blockquote>

Quatro passos, todos dentro de `make async`: (1) sobe `async_payload.csv` (50 linhas) para um caminho novo no S3, com o timestamp no nome do arquivo, para não colidir com uma execução anterior; (2) chama `InvokeEndpointAsync` passando esse URI como `InputLocation` — a chamada retorna **na hora**, com um `InferenceId` e a `OutputLocation` onde o resultado vai aparecer, mas a inferência ainda não rodou; (3) faz `HeadObject` em loop nesse exato `OutputLocation` (nunca um caminho montado à mão) até o objeto existir, com timeout de 600s; (4) baixa o conteúdo e conta quantas predictions vieram, comparando com as 50 linhas que subiram.

O ganho de arquitetura está no passo 2: quem chamou não fica esperando a resposta HTTP como no real-time; só recebe um "protocolo" (onde buscar o resultado) e segue a vida.

📚 Documentação oficial: [Asynchronous Inference](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference.html) e [Create an asynchronous inference endpoint](https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference-create-endpoint-create-endpoint-config.html).

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: tempo esgotado esperando o output no S3</b></summary>
<blockquote>

O comando faz polling no S3 por até 600 segundos. Se o endpoint estava com capacidade em 0 (autoscaling async), a primeira chamada depois de um tempo ocioso paga o custo de reprovisionar a instância antes de processar a fila, o que é mais lento que uma chamada síncrona de propósito. Rode `make async` de novo; se persistir além dos 600s, confira `make status` para o estado real do endpoint.

</blockquote>
</details>

---

<a id="passo-16"></a>

**16. Relacione o request com o output no S3**

```bash
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling
python3 -c "import json; d=json.load(open('artifacts/evidence/async.json')); print(d['input_uri']); print(d['output_uri']); print('capacidade antes/depois:', d['capacity_before'], d['capacity_after_observation'])"
```

> Saída esperada (URIs de uma execução real; os seus IDs serão diferentes):
> ```text
> s3://prb-cloud-ml-lab2-.../async/input/1787444194.csv
> s3://prb-cloud-ml-lab2-.../async/output/177e9993-d57f-4b68-8a02-d8689cd1cc93.out
> capacidade antes/depois: 1 1
> ```

A capacidade antes/depois é registrada como **observação**, não como critério de aprovação: o Async pode legitimamente variar entre 0 e 1 dependendo de quanto tempo se passou desde a última chamada.

---

<a id="passo-16-1"></a>

**16.1. Veja a fila sendo drenada no painel**

Recarregue o painel e olhe a **linha 2**, que é desta parte:

| Widget | O que procurar |
|---|---|
| "A fila do assíncrono está sendo drenada?" | uma linha **reta em zero** |
| "Chegou trabalho sem instância para atender?" | zero, ou um degrau em 1 se a sua chamada pegou o endpoint com capacidade zero |

> [!IMPORTANT]
> **Reta em zero é o resultado correto, não falha.** Fila só existe quando a chegada supera a drenagem, e aqui ela não supera: uma requisição de 50 linhas é processada em bem menos de um segundo, então o item entra e sai entre duas amostragens da métrica. Medimos isso de propósito — submetendo dez requisições seguidas, várias vezes, a fila continuou cravada em zero.

O que esse par de widgets responde, então, é: **a capacidade está dando conta?** Aqui está. Num volume de produção, com arquivos grandes chegando em rajada, é nesse gráfico que a fila apareceria — e é por isso que ele existe no painel.

A evidência de que o assíncrono funcionou não é o painel: é a saída do Passo 15 e o objeto no S3, que você já conferiu. Para ver algo se mover por causa do assíncrono, olhe a CPU do endpoint dele no widget "As instâncias estão de pé?", na linha 4: é ali que a máquina aparece enquanto processa e desaparece quando a capacidade volta a zero.

Este par de widgets é a razão de o assíncrono existir. No real-time a espera do cliente é a latência; aqui a espera é **fila**, e fila é uma coisa que se olha, não que se estima. Se o segundo widget marcou 1, você viu ao vivo a política `async-target-from-zero` fazendo o trabalho dela: chegou pedido, não havia máquina, e o endpoint subiu uma por causa desse sinal.

Se o segundo widget ficou em zero o tempo todo, seu endpoint ainda estava com uma instância de pé quando você chamou — o que é igualmente correto e só significa que não houve espera por capacidade.

![](img/painel-fila.png)

---

<a id="passo-17"></a>

**17. Rode o batch transform**

```bash
make batch
```

> [!IMPORTANT]
> Este comando cria um `TransformJob` efêmero e pode levar alguns minutos. Não é um endpoint — o recurso desaparece quando o job termina.

<details>
<summary><b>💡 Clique para entender: por que o batch não passa pelo Terraform</b></summary>
<blockquote>

Todos os outros recursos deste lab são **persistentes por design** (mesmo o async, que pode ir a zero, continua existindo como endpoint). Um `TransformJob` é o oposto: nasce, processa e morre sozinho, sem nada para o Terraform "gerenciar" entre uma execução e a próxima, por isso ele é criado direto via Boto3 (`CreateTransformJob`), com um nome carimbado com o timestamp, e não aparece em nenhum `.tf`.

Mecânica: sobe `batch_input.csv` (as mesmas 600 linhas de `test_features.csv`) para um prefixo novo no S3; chama `CreateTransformJob` apontando para esse prefixo, com `MaxConcurrentTransforms=1` e `MaxPayloadInMB=1` (o produto dos dois tem que ser `<= 100`, exigência da API) e `BatchStrategy=MultiRecord` (agrupa várias linhas por mini-lote em vez de uma chamada por linha); espera `DescribeTransformJob` até `Completed`; e por fim **lista** o prefixo de saída no S3 em vez de assumir o nome do arquivo de resultado — o SageMaker decide esse nome, e o lab só confia no que a API realmente gravou.

📚 Documentação oficial: [Use Batch Transform to Get Inferences](https://docs.aws.amazon.com/sagemaker/latest/dg/batch-transform.html) — a seção sobre `MaxPayloadInMB`/`MaxConcurrentTransforms` explica o limite de 100 citado acima.

</blockquote>
</details>

> Saída esperada (nome do job e duração mudam a cada execução):
> ```text
> [batch] criando o transform job prb-cloud-ml-lab2-batch-1787444210
> [batch] prb-cloud-ml-lab2-batch-1787444210: InProgress
> [batch] prb-cloud-ml-lab2-batch-1787444210: Completed
> [batch] output_count=600 duração_observada=119.725s
> ```


![](img/08-make-batch.png)

<details>
<summary><b>⚠ Se der erro: job de transform ficando muito tempo em <code>InProgress</code></b></summary>
<blockquote>

O Batch Transform provisiona a própria instância antes de processar, então os primeiros minutos são só provisionamento, não processamento. O comando espera até 900 segundos antes de reportar erro; se travar antes disso, é comportamento normal do serviço, não do script.

</blockquote>
</details>

---

<a id="passo-17-1"></a>

**17.1. Confirme no painel que a máquina do batch existiu e desapareceu**

Recarregue o painel e, ainda na **linha 2**, olhe o widget mais à direita, "A máquina do batch existiu e desapareceu?".

O que procurar é a **forma da curva**, não o valor: uma série que começa, dura alguns minutos e termina. Os três endpoints continuam de pé no painel; esta máquina não. É a diferença entre pagar por capacidade disponível e pagar por trabalho feito.

A CPU vai aparecer baixa, e isso é honesto: 600 linhas não cansam uma instância. O que o widget prova é a **existência e o fim** do recurso, não que ele tenha se esforçado.

Se o widget estiver completamente vazio logo depois do job terminar, recarregue depois de um ou dois minutos: a métrica de transform job é publicada com atraso, e vazio nos primeiros instantes não significa que algo falhou.

<details>
<summary><b>💡 Clique para entender: por que este widget precisa de uma busca em vez de um nome fixo</b></summary>
<blockquote>

Os outros widgets apontam para um endpoint por nome, e o nome do endpoint não muda durante o ciclo de vida do laboratório. O Batch Transform não tem endpoint: a métrica dele vive no namespace `/aws/sagemaker/TransformJobs`, com a dimensão `Host` no formato `<nome-do-job>/<instance-id>`. Esse nome carrega um timestamp e muda **a cada** `make batch`.

Por isso o widget usa uma expressão `SEARCH` pelo prefixo do laboratório em vez de uma dimensão fixa: assim ele encontra o job da execução de hoje e continuaria encontrando o de amanhã, sem ninguém editar o Terraform entre uma execução e outra.

📚 Documentação oficial: [Usar expressões de busca em gráficos](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/using-search-expressions.html) — a sintaxe completa do `SEARCH`, inclusive como restringir por namespace e por dimensão.

</blockquote>
</details>

![](img/painel-batch.png)

---

<a id="passo-18"></a>

**18. Compare async e batch com a evidência da sua execução**

```bash
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling
make resumo
code DECISION.md
```

O `make resumo` já atualizou as linhas do assíncrono e do batch na tabela. Escreva as seções **Arquivo assíncrono** e **Campanha noturna** da Recomendação. A pergunta que importa: por que o batch não precisa de endpoint e o async precisa?

### Checkpoint

- [x] `async.json`: `output_count == input_count` (50).
- [x] `batch.json`: `output_count == 600`, `status == Completed`.
- [x] No painel, a linha 2 mostra a fila voltando a zero e a máquina do batch já encerrada.
- [x] Você comparou os dois no `DECISION.md`.

---

## Parte 6 - Concorrência e elasticidade

### Resultado esperado desta parte

Uma matriz de carga com sucesso acima de 99%, e uma demonstração provada de elasticidade 1→2→1 no real-time.

<a id="passo-19"></a>

**19. Rode o teste de carga no real-time**

```bash
make load DURACAO=180
```

> Saída esperada (três níveis, valores medidos numa execução real; os seus vão variar):
> ```text
> [load] concurrency=1   requests=40   success_rate=1.0 p50=449.262ms p95=490.911ms rps=2.18
> [load] concurrency=4   requests=80   success_rate=1.0 p50=440.839ms p95=489.518ms rps=8.89
> [load] concurrency=8   requests=120  success_rate=1.0 p50=435.739ms p95=464.961ms rps=17.82
> ```

![](img/09-make-load.png)

O critério de aprovação é `success_rate >= 0.99` em todos os níveis; a latência é registrada, não comparada contra um número fixo.

<details>
<summary><b>💡 Clique para entender: como o teste de carga dispara as chamadas</b></summary>
<blockquote>

Para cada nível da matriz (concorrência 1/40 requests, 4/80, 8/120), o comando abre um `ThreadPoolExecutor` com **N** threads (N = a concorrência do nível) e submete todas as requisições de uma vez — o pool garante que nunca mais que N chamadas estejam em voo ao mesmo tempo. Cada chamada envia **uma linha** de `test_features.csv` (não as 5 fixas do `compare`) e mede o tempo de parede da chamada, sucesso ou falha. Ao final do nível: `success_rate` é a fração de chamadas sem exceção; `p50`/`p95`/`p99` vêm da mesma interpolação usada no `compare`; e `requests_per_second` é simplesmente `requests / tempo_total_do_nível`, não a soma dos tempos individuais — é por isso que o RPS cresce com a concorrência mesmo com a latência por chamada estável: mais chamadas acontecendo ao mesmo tempo, não chamadas mais rápidas.

📚 Documentação oficial: [Load test and optimize an endpoint](https://docs.aws.amazon.com/sagemaker/latest/dg/endpoint-scaling-loadtest.html) — a AWS recomenda esse mesmo tipo de teste antes de calibrar o `target_value` de uma política de scaling.

</blockquote>
</details>

---

<a id="passo-19-1"></a>

**19.1. Veja a carga se distribuindo no painel**

Recarregue o painel e olhe o widget "A carga se distribuiu?", na **linha 3**.

São duas séries: chamadas no total e chamadas por instância. Com **uma** instância as duas ficam exatamente em cima uma da outra — a instância recebe tudo.

Com `DURACAO=180` cada nível de concorrência ocupa cerca de um minuto, então o gráfico desenha **três degraus**, um por nível. Numa execução real foram 73, 247 e 659 chamadas por minuto, e a linha laranja do alvo (60 por instância) é cruzada nos três.

> [!IMPORTANT]
> Três minutos acima do alvo é violação **sustentada**, e é isso que a política de target tracking espera para agir. Numa execução real o alarme foi para `ALARM` cerca de dois minutos depois do comando terminar, e o endpoint subiu para **duas instâncias** por conta própria. Isso não é defeito: é a política funcionando, e é a melhor coisa que você vai ver nesta parte. Guarde a observação — o Passo 21 força o mesmo movimento de forma controlada, para você não depender do tempo do alarme.

Duas consequências práticas dessa reação:

- **custo**: a segunda instância cobra enquanto existir, e o scale-in natural é conservador (leva mais de dez minutos);
- **ordem dos passos**: o `make scale-demo` precisa começar com uma instância. Se ele encontrar duas, normaliza sozinho antes de demonstrar e diz isso no log, então você não precisa esperar nem fazer nada.

Se o widget estiver vazio logo depois do comando, recarregue depois de um ou dois minutos: a métrica é publicada com atraso próprio.


![](img/painel-distribuicao.png)

---

<a id="passo-20"></a>

**20. Interprete p50, p95 e RPS no DECISION.md**

```bash
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling
make resumo
code DECISION.md
```

A linha "Concorrência no atendimento" da tabela já traz os três níveis lado a lado. Volte à seção **Atendimento** e registre o que mudou entre concorrência 1 e concorrência 8: o p50 subiu? O p95 subiu mais que o p50? O RPS acompanhou a concorrência ou saturou? Não declare "melhor" por um único número: throughput e latência respondem perguntas diferentes.

---

<a id="passo-21"></a>

**21. Prove a elasticidade 1→2→1**

```bash
make scale-demo DURACAO=120
```

> [!CAUTION]
> Enquanto este comando roda, o real-time endpoint temporariamente tem **2 instâncias** cobrando. Ele mesmo restaura para 1 ao terminar; não interrompa.

> Saída esperada:
> ```text
> [scale] antes: 1
> [scale] subindo MinCapacity/MaxCapacity para 2 para forçar um scale-out determinístico
> [scale] escalado: 2
> [scale] restaurando MinCapacity=1, MaxCapacity=2 (valores gerenciados pelo Terraform, sem deixar drift)
> [scale] forçando DesiredInstanceCount de volta para 1: baixar só o MaxCapacity não faz o Application Auto Scaling reduzir, isso só acontece quando o alarme de target tracking avalia
> [scale] restaurado: 1
> ```


![](img/10-make-scale-demo.png)

A espera de cada transição pode variar; o timeout é de até 600 segundos por transição.

<details>
<summary><b>💡 Clique para entender: por que restaurar a capacidade não é só "abaixar o Max de volta"</b></summary>
<blockquote>

Subir de 1 para 2 funciona só com `RegisterScalableTarget`: quando o `MinCapacity` novo é maior que a capacidade atual, o Application Auto Scaling agenda a ação de scale-out sozinho. Descer de 2 para 1 **não é simétrico**: abaixar o `MaxCapacity` de volta para o valor gerenciado pelo Terraform não faz o serviço encolher a capacidade atual — isso só aconteceria quando a política de target tracking avaliasse o alarme de baixa utilização, o que pode levar mais que os 600 segundos do timeout desta demonstração.

Por isso o comando força o `DesiredInstanceCount` diretamente via `UpdateEndpointWeightsAndCapacities` (a mesma API que a política de scaling usaria por trás dos panos) e só então confirma que o Terraform e a AWS concordam nos limites (`min=1`, `max=2`).

📚 Documentação oficial: referência da API [`UpdateEndpointWeightsAndCapacities`](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_UpdateEndpointWeightsAndCapacities.html) e [`RegisterScalableTarget`](https://docs.aws.amazon.com/autoscaling/application/APIReference/API_RegisterScalableTarget.html) do Application Auto Scaling.

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: demora muito para sair de 2 e voltar a 1</b></summary>
<blockquote>

SageMaker provisiona/desprovisiona instância de verdade, não é instantâneo. Se passar de 600 segundos, o comando termina com erro explícito; rode `make status` para ver o estado real antes de repetir.

</blockquote>
</details>

---

<a id="passo-21-1"></a>

**21.1. Veja a elasticidade desenhada no painel**

Este é o widget-âncora do laboratório: "Quantas instâncias o atendimento tem agora? (1 → 2 → 1)", na **linha 3**.

O `make scale-demo` acabou de provar a subida e a volta por `DescribeEndpoint`, no terminal. Aqui você vê a mesma coisa como **forma**: a linha sai de 1, vai a 2 e volta a 1, com a linha cinza marcando o teto do autoscaling. Para levar essa evidência à Helena, um gráfico que sobe e desce vale mais que três linhas de log.

> [!IMPORTANT]
> Espere **cerca de um minuto** depois do comando terminar antes de recarregar; se o widget ainda estiver reto em 1, recarregue de novo depois de outro minuto. E não espere precisão de cronômetro: a janela real com duas instâncias dura pouco de propósito (numa execução medimos cerca de 45 segundos), mas a métrica tem granularidade de 60 segundos e a instância que sai continua reportando por alguns minutos — então o degrau no gráfico aparece mais largo do que foi, e demora alguns minutos para voltar a 1. O gráfico conta a história certa; o cronômetro exato é a saída do Passo 21, no seu terminal.

Com `DURACAO=120` o comando faz duas coisas a mais, e as duas são para o painel: **segura as duas instâncias no ar por dois minutos** (sem isso o degrau sai com um ponto só, porque a métrica de host publica um ponto por minuto) e **mantém tráfego leve ao fundo** durante todo o ciclo.

O tráfego de fundo é o que faz o widget vizinho, "A carga se distribuiu?", finalmente mostrar a distribuição. Com uma instância as duas séries coincidem; com duas atendendo, a série "por instância" cai para perto da **metade** do total. Numa execução real o minuto com duas instâncias marcou 42 chamadas no total e 21 por instância — exatamente 2,00x. É a prova de que a segunda máquina não está só existindo, está atendendo.

> [!NOTE]
> A contagem de instâncias pode **oscilar** de um minuto para outro no meio do degrau: ela vem do número de instâncias que reportaram CPU no minuto, e um minuto de transição pode ler 1 enquanto a segunda máquina entra ou sai. Se você vir um vale no meio do platô, não é erro seu.

O widget "As instâncias estão de pé? (CPU %)", na linha 4, é o complemento: é lá que você confirma que o assíncrono realmente desligou quando a capacidade voltou a zero — a série simplesmente deixa de ter dado.

<details>
<summary><b>💡 Clique para entender: de onde sai a contagem de instâncias, se o SageMaker não publica essa métrica</b></summary>
<blockquote>

Não existe métrica "número de instâncias" no SageMaker. O que existe é `CPUUtilization` no namespace `/aws/sagemaker/Endpoints`, que é métrica de **host**: cada instância de pé publica um ponto por minuto, receba chamada ou não. O widget usa a estatística `SampleCount` dessa métrica — ou seja, conta **quantos pontos chegaram** no minuto, que é o mesmo que contar quantas instâncias estavam vivas.

A primeira versão deste painel calculava a contagem de outra forma, dividindo `Invocations` por `InvocationsPerInstance`. A divisão é aritmeticamente exata, e foi descartada por um motivo prático: o `make scale-demo` sobe a capacidade **sem gerar tráfego**, então a divisão não tinha dado justamente no minuto da curva. O widget ficava vazio no momento em que ele existe para mostrar.

📚 Documentação oficial: [Estatísticas do CloudWatch](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Statistics-definitions.html) — a definição de `SampleCount` e das outras estatísticas, e por que a escolha da estatística muda o que o mesmo gráfico significa.

</blockquote>
</details>

> 📸 **Nota do autor (não é tarefa sua)** — capturar o widget da contagem de instâncias com a curva 1 → 2 → 1 completa. É a imagem que resume a Parte 6.
<!-- ![](img/painel-elasticidade.png) -->

---

<a id="passo-22"></a>

**22. Confirme que não sobrou drift na configuração**

```bash
make status
```

> Saída esperada (repare que os valores de scaling voltaram ao que o Terraform gerencia):
> ```json
> "scaling": {"realtime": {"min_capacity": 1, "max_capacity": 2, ...}}
> ```

Se `make plan` fosse rodado agora, ele diria `No changes` para o scalable target: a demonstração não deixou o Terraform e a AWS divergentes.

### Checkpoint

- [x] `load.json`: `success_rate >= 0.99` nos três níveis.
- [x] `scale.json`: `before=1`, `scaled=2`, `restored=1`.
- [x] No painel, a linha 3 mostra a carga ultrapassando o alvo sem a política reagir, e a contagem de instâncias subindo para 2 e voltando para 1.
- [x] `make status` confirma `min=1, max=2` de volta.

---

## Parte 7 - Dossiê e decisão

### Resultado esperado desta parte

Um dossiê que prova, elo por elo, a cadeia completa, e um `DECISION.md` terminado.

<a id="passo-23"></a>

**23. Gere o dossiê de evidência**

```bash
make evidence
```

> Saída esperada:
> ```text
> [evidence] chain_complete=True
> ```

![](img/11-make-evidence.png)

O `chain_complete` só fica `True` se **todas** as afirmações anteriores (treino completo, três endpoints `InService`, predictions equivalentes, async e batch corretos, load acima de 99%, scale-demo 1→2→1) ainda se sustentarem numa consulta fresca à API, não um checklist marcado de memória.

<details>
<summary><b>💡 Clique para entender: de onde vem cada afirmação do dossiê</b></summary>
<blockquote>

| Afirmação | Fonte |
|---|---|
| Artefato do treino | `DescribeTrainingJob.ModelArtifacts.S3ModelArtifacts` |
| Status de cada endpoint | `DescribeEndpoint` |
| Configuração serverless | `DescribeEndpointConfig` |
| Scaling | Application Auto Scaling `DescribeScalableTargets` + `DescribeScalingPolicies` |
| Output do async | URI do S3 + contagem de predictions |
| Output do batch | `DescribeTransformJob` + contagem de predictions no S3 |
| Latência | relógio monotônico do cliente, amostrado |

Nenhuma linha do dossiê é print de tela.

</blockquote>
</details>

---

<a id="passo-24"></a>

**24. Complete a recomendação para Helena**

```bash
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling
make resumo
code DECISION.md
```

Esta é a última passada do `make resumo`: agora as seis linhas da tabela de evidências têm dado medido, incluindo carga e elasticidade, que só existiram depois da Parte 6. Termine as quatro seções de recomendação (uma por workload), a seção "Custo do erro" e "Condições que fariam a decisão mudar".

A essa altura o documento não tem mais nenhum dado para você transcrever: a tabela veio preenchida, e o que falta é exclusivamente o seu julgamento.

O painel continua aberto, e ele é a outra metade da evidência: o `summary.md` tem o número, o painel tem a forma. Use os dois — para cada linha da tabela de evidências do `DECISION.md`, o widget correspondente é:

| Linha da tabela | Widget que sustenta o argumento |
|---|---|
| Atendimento humano | "Quem responde mais rápido" e "Quanto custa não ter instância de pé" |
| App após fechamento da fatura | os mesmos dois, lendo a série do serverless |
| Importação de arquivo pesado | "A fila do assíncrono está sendo drenada?" |
| Campanha noturna | "A máquina do batch existiu e desapareceu?" |

Para defender uma escolha diante de quem decide, a forma costuma convencer mais rápido que a tabela — e as duas coisas vêm da mesma execução, então não há conflito entre elas.

> [!TIP]
> A seção mais valiosa é "Custo do erro". Escolher real-time para um workload esporádico não quebra nada tecnicamente, só infla a fatura. Escolher serverless para atendimento síncrono de alto volume não quebra nada tecnicamente — só empurra latência de cold start para o cliente errado.

### Checkpoint

- [x] `artifacts/evidence/summary.md` mostra `Cadeia completa: sim`.
- [x] `DECISION.md` tem as quatro recomendações, custo do erro e condições preenchidos.

---

## Parte 8 - Encerramento obrigatório

### Resultado esperado desta parte

Zero recursos cobrando, provado por API.

> [!CAUTION]
> **Esta parte não é opcional.** O real-time e o async cobram enquanto existirem, inclusive com o Codespaces desligado. O crédito do Learner Lab não é reposto.

<a id="passo-25"></a>

**25. Destrua tudo**

```bash
make destroy
```

> Saída esperada no fim:
> ```text
> Destroy complete! Resources: 22 destroyed.
> ```

![](img/12-make-destroy.png)

Vinte e dois: os nove do estágio 1 (bucket, suas três configurações de segurança, quatro objetos S3 e o training job) mais os treze do estágio 2 (model, três endpoint configs, três endpoints, dois scalable targets, três políticas de scaling e um alarme). O Terraform destrói na ordem inversa da criação: os endpoints e políticas de scaling saem antes do bucket.

<details>
<summary><b>💡 Clique para entender: como o Terraform decide a ordem de destruição</b></summary>
<blockquote>

`make destroy` é só `terraform destroy -auto-approve`. A ordem que você vê na saída não está escrita em nenhum lugar do código: o Terraform constrói um grafo de dependências a partir das próprias referências entre recursos (o `Endpoint` referencia o `EndpointConfiguration`, que referencia o `Model`, que referencia o artefato no bucket) e destrói sempre uma folha do grafo antes do que ela depende. É o mesmo grafo que decide a ordem de **criação**, só percorrido de trás para frente — por isso o comando nunca tenta apagar um bucket que ainda tem um `Model` apontando para dentro dele.

📚 Documentação oficial: [Resource dependencies](https://developer.hashicorp.com/terraform/language/resources/behavior#resource-dependencies) e [The Dependency Graph](https://developer.hashicorp.com/terraform/internals/graph) no manual do Terraform.

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: o destroy falha porque o bucket não está vazio</b></summary>
<blockquote>

Aconteceu algo fora do Terraform (por exemplo, um segundo `make batch` cujo output ainda não foi limpo). O `destroy` do bucket já roda `aws s3 rb --force`, que esvazia antes de apagar; se ainda assim falhar, esvazie explicitamente e rode de novo:

```bash
cd /workspaces/FIAP-Cloud-Based-Machine-Learning/03-serving-and-scaling
BUCKET=$(terraform -chdir=terraform output -raw bucket_name 2>/dev/null || echo "")
[ -n "$BUCKET" ] && aws s3 rm "s3://$BUCKET" --recursive
make destroy
```

</blockquote>
</details>

<details>
<summary><b>⚠ Se der erro: <code>ExpiredToken</code> durante o destroy</b></summary>
<blockquote>

**Este é o cenário mais perigoso do laboratório**: a credencial vencer no meio do destroy deixa endpoint no ar cobrando. Renove a credencial e rode `make destroy` de novo imediatamente. Depois, confirme com o Passo 26 — não presuma que deu certo.

</blockquote>
</details>

---

<a id="passo-26"></a>

**26. Prove que não sobrou nada cobrando**

```bash
make verify-clean
```

> Saída esperada:
> ```text
>   [PASS] no_endpoints_for_prefix
>   [PASS] no_endpoint_configs_for_prefix
>   [PASS] no_models_for_prefix
>   [PASS] no_scalable_targets_for_prefix
>   [PASS] no_scaling_policies_for_prefix
>   [PASS] no_cloudwatch_alarms_for_prefix
>   [PASS] no_cloudwatch_dashboards_for_prefix
>   [PASS] no_lab_bucket
>   [PASS] no_active_training_or_transform_jobs
> [PASS] verificação de limpeza
> ```


![](img/13-verify-clean.png)

Este comando **não olha o state do Terraform**: ele pergunta direto às APIs se sobrou endpoint, config, modelo, scalable target, política, alarme ou bucket com o prefixo `prb-cloud-ml-lab2`. Um training/transform job antigo com status `Completed` pode continuar listado — isso não é falha, porque não é um recurso ativo faturável.

<details>
<summary><b>⚠ Se der erro: <code>verify-clean</code> encontra endpoint que o Terraform não conhece</b></summary>
<blockquote>

Acontece se algo criou um recurso com o mesmo prefixo por fora do Terraform (por exemplo, um teste manual pelo console). O nome exato aparece em `details.endpoints`/`details.models` na saída JSON. Apague pelo nome reportado:

```bash
aws sagemaker delete-endpoint --endpoint-name <nome-reportado>
make verify-clean
```

</blockquote>
</details>

### Checkpoint

- [x] `Destroy complete!`
- [x] `make verify-clean` responde `[PASS] verificação de limpeza` com os nove itens.

**Zero recursos cobrando.**

---

## Conclusão

Você começou com uma pergunta sobre custo e complexidade e terminou com quatro respostas diferentes para quatro workloads diferentes, todas saindo do mesmo artefato treinado uma única vez.

Três ideias sobrevivem ao laboratório:

<dl>
  <dt><b>Não existe padrão de serving universalmente melhor</b></dt>
  <dd>Real-time, serverless, async e batch respondem contratos diferentes de SLA, volume e tolerância a espera. Escolher pelo que "parece mais moderno" em vez de pelo contrato do workload é o erro mais caro deste tema.</dd>
  <dt><b>Scaling automático e demonstração de elasticidade são coisas distintas</b></dt>
  <dd>O target tracking real fica configurado; a prova em aula do 1→2→1 é controlada de propósito, porque esperar CloudWatch reagir a tráfego real tornaria a aula dependente do relógio da AWS.</dd>
  <dt><b>Um artefato pode sustentar contratos de consumo diferentes</b></dt>
  <dd>O mesmo <code>model.tar.gz</code> alimentou três endpoints e um batch job. O que mudou entre eles foi a receita de capacidade, nunca o modelo.</dd>
</dl>

## Próximo passo

O laboratório **04 - ML Operations** continua desta arquitetura e ataca observabilidade, confiabilidade e segurança de um sistema de ML em produção. Ele será liberado na pasta `04-ml-operations`.

---

<details>
<summary><b>💡 Glossário rápido</b></summary>
<blockquote>

| Termo | O que é neste laboratório |
|---|---|
| **Real-Time Endpoint** | instância sempre ligada, latência baixa e previsível, cobra 24/7 |
| **Serverless Inference** | AWS gerencia a capacidade; paga por invocação; tem comportamento de first-request |
| **Asynchronous Inference** | request/resposta desacoplados via S3; pode escalar a zero |
| **Batch Transform** | job efêmero, sem endpoint persistente, processa um lote e termina |
| **Target tracking** | política de Application Auto Scaling que reage a uma métrica (ex.: invocações por instância) |
| **`SageMakerVariantInvocationsPerInstance`** | métrica usada pelo target tracking do real-time |
| **`ApproximateBacklogSizePerInstance`** | métrica usada pelo target tracking do async |
| **`HasBacklogWithoutCapacity`** | alarme do CloudWatch que dispara o scale-from-zero do async |
| **`ModelLatency`** | tempo que o modelo leva para responder, medido dentro do contêiner |
| **`OverheadLatency`** | tempo que o SageMaker gasta **fora** do modelo (roteamento e, no serverless, preparação do ambiente) |
| **`CPUUtilization`** | métrica de host: cada instância de pé publica um ponto por minuto, com ou sem chamada |
| **p50 / p95 / p99** | percentis de latência: 50%, 95% e 99% das chamadas responderam em até esse tempo |
| **RPS** | requests por segundo, medida de throughput |
| **`LabRole`** | role pré-existente do AWS Academy que o SageMaker assume |

</blockquote>
</details>

<details>
<summary><b>💡 Onde pedir ajuda</b></summary>
<blockquote>

1. Releia o bloco `⚠ Se der erro` do passo em que você travou.
2. Rode `make doctor`. Cobre credencial vencida e região errada, as duas causas mais comuns.
3. Se travar com custo em aberto (endpoint no ar), rode `make destroy && make verify-clean` **antes** de pedir ajuda.
4. Ao relatar, traga: o número do passo, o comando exato, a saída completa do erro e o resultado de `make doctor`. Nunca cole credencial.

</blockquote>
</details>
