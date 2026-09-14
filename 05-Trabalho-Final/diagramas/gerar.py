#!/usr/bin/env python3
"""Gerador do diagrama de arquitetura — Trabalho Final (Bora Fibra).

Copiado/estendido de diagramas/gerar.py do lab 04.2 (que copiou o template
scripts/gerar-template.py da skill excalidraw-aulas). Mesma tecnica de
blindagem de rotulo (retangulo branco opaco atras do texto, L18) e o mesmo
flag --passe1 para gerar a variante SEM setas usada na prova de OCR (a seta
tem roundness type 2 — nenhum verificador por segmentos retos modela o bojo
da curva, entao a prova real e comparar o texto lido nas duas variantes).

Regra dura desta tarefa (08_DIAGRAM_CONTRACT.md): os nos de serving usam
rotulo neutro ("pattern escolhido pelo grupo") — nunca nomeiam qual dos dois
candidatos de cada workload foi escolhido. Por isso os dois nos de serving
usam o MESMO icone, a MESMA cor e o MESMO peso visual; a diferenca entre eles
e so o nome do workload (atendimento vs campanha), nunca do pattern.
"""
import base64
import json
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
ICONES = os.path.join(DIR, "icones")

# Paleta: fonte de verdade e a skill excalidraw-aulas (SKILL.md) + o contrato
# de diagramas do Trabalho Final (08_DIAGRAM_CONTRACT.md, secao "Semantica
# visual"). CINZA e a cor default de toda label informativa neutra — como
# nos tres diagramas aprovados da serie, so os poucos elementos que o
# contrato marca explicitamente ganham uma cor semantica propria.
FIAP_MAGENTA = "#ED0973"   # so titulo (L13 da skill)
CINZA = "#495057"          # default: nos e setas neutras
VIOLETA = "#7048e8"        # artifact (model.tar.gz / SageMaker Model)
VERDE = "#0ca678"          # serving (atendimento e campanha, mesmo peso)
LARANJA = "#e8590c"        # decisao (DECISION.md)
ALERTA = "#d13212"         # alarme/incidente (mesmo vermelho do lab 04.1)

ICON = 80
LABEL_H = 64
LABEL_GAP = 12


def _seed(n):
    return 1000 + n * 7


def carrega_icone_datauri(nome):
    with open(os.path.join(ICONES, f"{nome}.png"), "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


class Diagrama:
    def __init__(self):
        self.elements = []
        # rotulos de no sao emitidos por ultimo (ver salvar): setas saem da
        # borda do icone e passam perto de onde o rotulo mora, entao so o
        # z-order mantem o texto legivel por cima do traco da seta.
        self.rotulos_no = []
        self.files = {}
        self._n = 0

    def _id(self, prefix):
        self._n += 1
        return f"{prefix}{self._n}"

    # ------------------------------------------------------------------ nos
    def no(self, icone, rotulo, cx, cy, cor=CINZA, lbl_w=230, escudo_w=None):
        x = cx - ICON / 2
        y = cy - ICON / 2
        file_id = self._id("file")
        self.files[file_id] = {
            "mimeType": "image/png", "id": file_id,
            "dataURL": carrega_icone_datauri(icone), "created": 1,
        }
        img_id = self._id("img")
        self.elements.append({
            "id": img_id, "type": "image", "x": x, "y": y,
            "width": ICON, "height": ICON, "angle": 0,
            "strokeColor": "transparent", "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
            "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
            "roundness": None, "seed": _seed(self._n), "version": 1,
            "versionNonce": _seed(self._n), "isDeleted": False,
            "boundElements": [], "updated": 1, "link": None, "locked": False,
            "status": "saved", "fileId": file_id, "scale": [1, 1],
        })
        lbl_x = cx - lbl_w / 2
        lbl_y = y + ICON + LABEL_GAP
        if escudo_w:
            # L18: placa branca opaca atras do rotulo de NO. escudo_w vem da
            # tinta real da linha mais longa (medida por pixel na cena SEM
            # setas) + 12px — nao de lbl_w, que e mais largo que a tinta e
            # apagaria pedaco do icone vizinho se usado como largura da placa.
            self.rotulos_no.append({
                "id": f"shield{self._n}", "type": "rectangle",
                "x": cx - escudo_w / 2, "y": lbl_y,
                "width": escudo_w, "height": 40, "angle": 0,
                "strokeColor": "transparent", "backgroundColor": "#ffffff",
                "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
                "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
                "roundness": None, "seed": _seed(self._n), "version": 1,
                "versionNonce": _seed(self._n), "isDeleted": False,
                "boundElements": [], "updated": 1, "link": None,
                "locked": False,
            })
        self.rotulos_no.append({
            "id": self._id("txt"), "type": "text", "x": lbl_x, "y": lbl_y,
            "width": lbl_w, "height": LABEL_H, "angle": 0,
            "strokeColor": cor, "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
            "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
            "roundness": None, "seed": _seed(self._n), "version": 1,
            "versionNonce": _seed(self._n), "isDeleted": False,
            "boundElements": [], "updated": 1, "link": None, "locked": False,
            "fontSize": 21, "fontFamily": 2, "text": rotulo,
            "textAlign": "center", "verticalAlign": "top",
            "containerId": None, "originalText": rotulo, "lineHeight": 1.25,
            "baseline": 14,
        })
        return {"x": x, "y": y, "w": ICON, "h": ICON, "cx": cx, "cy": cy}

    @staticmethod
    def borda(bbox, lado):
        if lado == "D":
            return bbox["x"] + bbox["w"], bbox["cy"]
        if lado == "E":
            return bbox["x"], bbox["cy"]
        if lado == "T":
            return bbox["cx"], bbox["y"]
        if lado == "B":
            return bbox["cx"], bbox["y"] + bbox["h"]
        raise ValueError(lado)

    # ------------------------------------------------- blindagem de rotulo
    def _rotulo_blindado(self, cx, cy, texto, cor=CINZA, fontSize=17, w=286,
                          h=28, align="center"):
        """L18: retangulo branco opaco ATRAS do texto do rotulo de seta."""
        rx = cx - w / 2
        ry = cy - h / 2
        self.elements.append({
            "id": self._id("rect"), "type": "rectangle", "x": rx, "y": ry,
            "width": w, "height": h, "angle": 0,
            "strokeColor": "transparent", "backgroundColor": "#ffffff",
            "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
            "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
            "roundness": None, "seed": _seed(self._n), "version": 1,
            "versionNonce": _seed(self._n), "isDeleted": False,
            "boundElements": [], "updated": 1, "link": None, "locked": False,
        })
        self.elements.append({
            "id": self._id("etxt"), "type": "text", "x": rx, "y": ry,
            "width": w, "height": h, "angle": 0,
            "strokeColor": cor, "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
            "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
            "roundness": None, "seed": _seed(self._n), "version": 1,
            "versionNonce": _seed(self._n), "isDeleted": False,
            "boundElements": [], "updated": 1, "link": None, "locked": False,
            "fontSize": fontSize, "fontFamily": 2, "text": texto,
            "textAlign": align, "verticalAlign": "middle",
            "containerId": None, "originalText": texto, "lineHeight": 1.25,
            "baseline": 11,
        })

    # ---------------------------------------------------------------- aresta
    def _arrow(self, x1, y1, x2, y2, cor, tracejada, strokeWidth):
        self.elements.append({
            "id": self._id("arr"), "type": "arrow", "x": x1, "y": y1,
            "width": x2 - x1, "height": y2 - y1, "angle": 0,
            "strokeColor": cor, "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": strokeWidth,
            "strokeStyle": "dashed" if tracejada else "solid",
            "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
            "roundness": {"type": 2}, "seed": _seed(self._n), "version": 1,
            "versionNonce": _seed(self._n), "isDeleted": False,
            "boundElements": [], "updated": 1, "link": None, "locked": False,
            "points": [[0, 0], [x2 - x1, y2 - y1]],
            "lastCommittedPoint": None, "startBinding": None,
            "endBinding": None, "startArrowhead": None, "endArrowhead": "arrow",
        })

    def liga(self, a, lado_a, b, lado_b, rotulo=None, cor=CINZA,
             tracejada=False, strokeWidth=2, frac=0.5, label_w=286,
             label_dx=None, label_dy=None, rotulo_cor=None):
        """Liga a borda `lado_a` de `a` a borda `lado_b` de `b`."""
        x1, y1 = self.borda(a, lado_a)
        x2, y2 = self.borda(b, lado_b)
        self._arrow(x1, y1, x2, y2, cor, tracejada, strokeWidth)
        if rotulo:
            horizontal = abs(x2 - x1) >= abs(y2 - y1)
            dx = label_dx if label_dx is not None else (0 if horizontal else 16)
            dy = label_dy if label_dy is not None else (-22 if horizontal else 0)
            mx = x1 + (x2 - x1) * frac + dx
            my = y1 + (y2 - y1) * frac + dy
            self._rotulo_blindado(mx, my, rotulo, cor=rotulo_cor or cor,
                                   w=label_w,
                                   align="left" if not horizontal else "center")

    # -------------------------------------------------------- texto/estrutura
    def titulo(self, texto, x, y, w=1600):
        self.elements.append({
            "id": self._id("title"), "type": "text", "x": x, "y": y,
            "width": w, "height": 30, "angle": 0, "strokeColor": FIAP_MAGENTA,
            "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 1, "strokeStyle": "solid", "roughness": 1,
            "opacity": 100, "groupIds": [], "frameId": None, "roundness": None,
            "seed": _seed(self._n), "version": 1, "versionNonce": _seed(self._n),
            "isDeleted": False, "boundElements": [], "updated": 1, "link": None,
            "locked": False, "fontSize": 28, "fontFamily": 2, "text": texto,
            "textAlign": "left", "verticalAlign": "top", "containerId": None,
            "originalText": texto, "lineHeight": 1.25, "baseline": 19,
        })

    def nota(self, texto, x, y, w=900, cor=CINZA, fontSize=18, align="left"):
        self.elements.append({
            "id": self._id("nota"), "type": "text", "x": x, "y": y,
            "width": w, "height": 40, "angle": 0, "strokeColor": cor,
            "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 1, "strokeStyle": "solid", "roughness": 1,
            "opacity": 100, "groupIds": [], "frameId": None, "roundness": None,
            "seed": _seed(self._n), "version": 1, "versionNonce": _seed(self._n),
            "isDeleted": False, "boundElements": [], "updated": 1, "link": None,
            "locked": False, "fontSize": fontSize, "fontFamily": 2, "text": texto,
            "textAlign": align, "verticalAlign": "top", "containerId": None,
            "originalText": texto, "lineHeight": 1.25, "baseline": 12,
        })

    def salvar(self, caminho):
        todos = self.elements + self.rotulos_no
        doc = {
            "type": "excalidraw", "version": 2, "source": "fiap-gerador",
            "elements": todos,
            "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
            "files": self.files,
        }
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        print(f"gerado: {caminho} ({len(todos)} elementos)",
              file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Layout — grade de colunas/linhas por coordenada (sem faixas/divisores/
# rodape de especificacao — nenhum dos diagramas aprovados da serie tem
# isso). Passo vertical 350px exatos (piso exigido pelo contrato: >= 350).
#
# yOps3 foi removida (correcao de coordenacao apos medicao real: a linha
# extra deixava o canvas mais alto que largo, razao largura/altura 0,77
# contra a faixa-alvo 1,2-1,5 dos quatro diagramas aprovados da disciplina).
# Os 4 nos da cauda ground_truth->evidence->decisao->zip_final agora moram
# todos em yOps2, em serpentina DIREITA->ESQUERDA reaproveitando as mesmas
# colunas xC..xF da linha yOps1 (leitura em S: indo para a direita em
# yOps1, voltando para a esquerda em yOps2) — nenhuma coluna nova.
#
# Passo horizontal 400 (subiu de 300/340): com 20 nos (contra 11-12 dos
# diagramas aprovados de referencia), manter a densidade dentro da faixa
# 190k-230k px^2/no exige mais area, nao menos — e alargar a grade e o jeito
# de crescer a area sem adicionar faixas vazias. 400px de passo ainda deixa
# folga >= 140px entre os escudos (a tinta real da label) de colunas
# vizinhas, mesmo nas labels mais largas (evidence, escudo_w=270).
# ---------------------------------------------------------------------------
xA, xB, xC, xD, xE, xF = 140, 540, 940, 1340, 1740, 2140
yAtend, ySpine, yCamp, yOps1, yOps2 = 180, 530, 880, 1230, 1580


def montar(com_setas: bool):
    d = Diagrama()

    d.titulo("Trabalho Final · Bora Fibra — do modelo a decisao operacional "
              "de retencao", 60, 30, w=1900)
    d.nota("Dados -> Treino -> Artefato -> dois workloads -> operacao -> "
           "decisao. Um so modelo, dois consumos, uma decisao de go-live.",
           60, 66, w=1900)

    # ---- esquerda: dados e treino (linha da espinha, ySpine) ----
    dataset = d.no("data-table", "Dataset Bora Fibra\n(splits deterministicos, seed fixa)",
                    xA, ySpine, lbl_w=310, escudo_w=300)
    s3dados = d.no("s3", "S3 - dataset\n(canais train/ e validation/)",
                    xB, ySpine, lbl_w=250, escudo_w=238)
    contrato = d.no("document", "Contrato de dados\n(schema + 7 features validadas)",
                     xC, ySpine, lbl_w=260, escudo_w=258)
    treino = d.no("sagemaker", "SageMaker Training Job\n(boto3, churn Bora Fibra)",
                   xD, ySpine, lbl_w=260, escudo_w=248)
    # artifact = violeta (semantica do contrato de diagramas)
    artefato = d.no("sagemaker", "model.tar.gz\nSageMaker Model (compartilhado)",
                     xE, ySpine, cor=VIOLETA, lbl_w=250, escudo_w=250)

    # ---- direita superior: atendimento (workload A) ----
    cliente_at = d.no("client", "Aplicacao de atendimento\n(consulta durante a ligacao)",
                       xD, yAtend, lbl_w=260, escudo_w=258)
    # serving = verde/ciano; rotulo NEUTRO — nao nomeia realtime/serverless.
    serving_at = d.no("sagemaker", "Serving - atendimento\npattern escolhido pelo grupo",
                       xE, yAtend, cor=VERDE, lbl_w=250, escudo_w=246)

    # ---- direita inferior: campanha (workload B) ----
    base_camp = d.no("s3", "Base da campanha\n(arquivo, 600 clientes)",
                      xD, yCamp, lbl_w=230, escudo_w=214)
    serving_camp = d.no("sagemaker", "Serving - campanha\npattern escolhido pelo grupo",
                         xE, yCamp, cor=VERDE, lbl_w=250, escudo_w=246)
    output_camp = d.no("s3", "Output da campanha\n(600 scores gravados)",
                        xF, yCamp, lbl_w=230, escudo_w=222)

    # ---- camada inferior de operacao (yOps1) ----
    referencia = d.no("s3", "S3 - referencia\n(baseline de producao)",
                       xA, yOps1, lbl_w=230, escudo_w=200)
    cw_metricas = d.no("cloudwatch", "CloudWatch - metricas\n(PSI, taxa de churn prevista)",
                        xB, yOps1, lbl_w=260, escudo_w=258)
    # alarme/incidente = alerta (semantica do contrato)
    cw_alarme = d.no("cloudwatch", "CloudWatch - alarme\n(PSI maximo >= 0,20)",
                      xC, yOps1, cor=ALERTA, lbl_w=250, escudo_w=210)
    eventbridge = d.no("eventbridge", "EventBridge\n(regra de reacao operacional)",
                        xD, yOps1, lbl_w=250, escudo_w=248)
    lambda_ = d.no("lambda", "Lambda drift_response\n(LabRole, nao retreina)",
                    xE, yOps1, lbl_w=250, escudo_w=228)
    incidente = d.no("s3", "S3 - incidente\n(JSON do evento aberto)",
                      xF, yOps1, cor=ALERTA, lbl_w=230, escudo_w=222)

    # ---- ground truth atrasado -> qualidade -> saida (yOps2) ----
    # Serpentina: yOps1 anda para a direita (referencia -> ... -> incidente),
    # yOps2 volta para a esquerda (ground_truth -> ... -> zip_final), mesmas
    # colunas xC..xF. Leitura em S, sem coluna nova alem da grade xA..xF.
    # ground_truth fica em xF (alinhado embaixo de incidente — seta vertical
    # T/B sem diagonal); dali em diante o fluxo anda para a esquerda.
    ground_truth = d.no("documents", "Ground truth atrasado\n(chega depois do incidente)",
                         xF, yOps2, lbl_w=250, escudo_w=246)
    evidence = d.no("metrics", "Evidence\n(F1 / ROC-AUC, artifacts/evidence/*.json)",
                     xE, yOps2, lbl_w=270, escudo_w=270)
    # decisao = laranja
    decisao = d.no("document", "DECISION.md\n(go-live, escrito pelo grupo)",
                    xD, yOps2, cor=LARANJA, lbl_w=250, escudo_w=214)
    zip_final = d.no("folder", "trabalho-final-cloud-ml.zip\n(pacote de entrega)",
                      xC, yOps2, lbl_w=250, escudo_w=250)

    if com_setas:
        # ---- espinha esquerda -> artefato ----
        d.liga(dataset, "D", s3dados, "E", "grava splits no data lake")
        d.liga(s3dados, "D", contrato, "E", "valida schema (7 features)")
        d.liga(contrato, "D", treino, "E", "dispara training job")
        d.liga(treino, "D", artefato, "E", "artefato via DescribeTrainingJob")

        # ---- bifurcacao para os dois workloads (rotulos distintos: qual
        # workload recebe o modelo, nunca qual pattern foi escolhido) ----
        d.liga(artefato, "T", serving_at, "B", "implanta no serving de atendimento")
        d.liga(artefato, "B", serving_camp, "T", "implanta no serving de campanha")

        # ---- atendimento: chamada individual e retorno na interacao ----
        d.liga(cliente_at, "D", serving_at, "E",
               "chamada individual -> retorno na\nmesma interacao (p50/p95)",
               label_w=260)

        # ---- campanha: arquivo de entrada e output com 600 scores ----
        d.liga(base_camp, "D", serving_camp, "E", "envia base da campanha")
        d.liga(serving_camp, "D", output_camp, "E", "grava 600 scores")

        # ---- operacao: monitoramento (tracejado cinza) ----
        d.liga(referencia, "D", cw_metricas, "E", "calcula PSI (data + prediction drift)",
               tracejada=True)
        # transicao para o caminho de incidente (alerta): a partir daqui,
        # tracejado + vermelho, igual ao lab 04.1 aprovado.
        d.liga(cw_metricas, "D", cw_alarme, "E", "compara com o limiar",
               tracejada=True, cor=ALERTA)
        d.liga(cw_alarme, "D", eventbridge, "E", "entrou em ALARM",
               tracejada=True, cor=ALERTA)
        d.liga(eventbridge, "D", lambda_, "E", "invoca (evento)",
               tracejada=True, cor=ALERTA)
        d.liga(lambda_, "D", incidente, "E", "escreve incidente (JSON)",
               tracejada=True, cor=ALERTA)

        # ---- depois do incidente: ground truth atrasado -> qualidade ----
        d.liga(incidente, "B", ground_truth, "T", "ground truth chega depois",
               tracejada=True)
        d.liga(ground_truth, "E", evidence, "D", "mede qualidade (F1 / ROC-AUC)")
        d.liga(evidence, "E", decisao, "D", "sustenta a decisao")
        d.liga(decisao, "E", zip_final, "D", "empacota entrega")

    d.nota("Solido = caminho do dado/artefato  ·  Tracejado = observabilidade  ·  "
           "Tracejado vermelho = caminho do incidente.",
           60, yOps2 + 220, w=1900, fontSize=18)
    d.nota("O aluno escolhe o pattern de cada workload em student/solution.yaml; "
           "este diagrama nao indica qual e o correto.",
           60, yOps2 + 265, w=1900, fontSize=18, cor=CINZA)

    return d


if __name__ == "__main__":
    com_setas = "--sem-setas" not in sys.argv
    d = montar(com_setas)
    saida = "arquitetura_sem_setas.excalidraw" if not com_setas else "arquitetura.excalidraw"
    d.salvar(os.path.join(DIR, saida))
