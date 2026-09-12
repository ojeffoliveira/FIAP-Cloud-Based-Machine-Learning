# 04 - Machine Learning Operations

Aula 3 do curso **Cloud-Based Machine Learning**: operação, confiabilidade,
observabilidade, MLOps e segurança.

Os labs 02 e 03 construíram e serviram a capacidade de churn da Bora Fibra. Este módulo
faz a pergunta que sobra depois do go-live: **como sabemos que ela continua correta?**

## Laboratórios

| Lab | Título | Estado |
|---|---|---|
| [04.1](01-observability-drift-response/README.md) | Observabilidade, drift e resposta operacional | Disponível |
| 04.2 | SLM no SageMaker | Laboratório futuro |

### 04.1 — Observabilidade, drift e resposta operacional

Prova na AWS real que um endpoint pode estar `InService`, responder normalmente e ainda
assim o sistema de ML estar degradado. O aluno mede drift de dados e de predições com
PSI, publica métricas customizadas no CloudWatch, acompanha um dashboard em tempo quase
real, vê um alarme virar incidente via EventBridge e Lambda, mede a queda de qualidade
quando o ground truth chega — e escreve a decisão sobre retreinar ou não.

Duração: ~60 minutos. Continua a linhagem `churn-v1` do Lab 02.

### 04.2 — SLM no SageMaker

Deploy de um *small language model* no SageMaker, aplicando as mesmas perguntas de
operação a um tipo de modelo em que "resposta certa" é bem mais difícil de definir.
Ainda não publicado.
