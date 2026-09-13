#!/usr/bin/env python3
"""Gerador do diagrama de arquitetura — Lab 04.2 (SLM no SageMaker).

Copiado/estendido de scripts/gerar-template.py da skill excalidraw-aulas.
Extensoes sobre o template generico: cor/tracejado por aresta (o template so
tinha tracejada em seta_vertical) e blindagem de rotulo com retangulo branco
opaco atras do texto (licao L18 — a seta tem roundness type 2, nenhum
verificador por segmentos modela o bojo da curva). O agrupamento em
esquerda/central/direita e feito so por coordenada, como nos diagramas
aprovados da serie: sem divisor, sem rotulo de faixa, sem `line`.
"""
import base64
import json
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
ICONES = os.path.join(DIR, "icones")

# Paleta: fonte de verdade e a skill excalidraw-aulas (SKILL.md)
FIAP_MAGENTA = "#ED0973"
# CINZA e a unica cor de texto do diagrama (rotulo de no, rotulo de seta, nota),
# como nos tres diagramas aprovados da serie: preto puro pesa mais na pagina.
CINZA = "#495057"

ICON = 80
LABEL_H = 50
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
        # rotulos de no sao emitidos por ultimo (ver salvar): setas verticais
        # saem da base do icone e passam exatamente onde o rotulo mora, entao
        # so o z-order mantem o texto legivel — a seta fica por baixo do glifo.
        self.rotulos_no = []
        self.files = {}
        self._n = 0

    def _id(self, prefix):
        self._n += 1
        return f"{prefix}{self._n}"

    # ------------------------------------------------------------------ nos
    def no(self, icone, rotulo, cx, cy, lbl_w=230, escudo_w=None):
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
            # L18 aplicada ao rotulo de NO (o rotulo de aresta ja nascia com
            # placa): so o z-order nao basta, a seta encosta na tinta do glifo e
            # o OCR passa a ler "Codegpaces". A placa e a MESMA do rotulo de
            # aresta — branca, sem borda, roughness 0.
            # escudo_w vem da tinta real da linha mais longa (metrica Helvetica
            # 16) + 12px, nao de lbl_w: a caixa declarada e mais larga que a
            # tinta e placa desse tamanho chega no icone vizinho (foi assim que
            # um escudo de 190 apagou 20x22px dos icones do EC2 e do IAM).
            # Altura = 2 linhas x 16 x 1.25 = 40, nao LABEL_H=50 (que carrega
            # uma linha fantasma): o buraco branco que a placa abre na seta que
            # passa por tras fica do tamanho do texto, nem um pixel mais.
            # id sem consumir o contador: `_seed` deriva de `self._n`, e gastar
            # um numero aqui mudaria o seed de TODAS as setas criadas depois,
            # redesenhando o tracado sketchy de roughness 1 do diagrama inteiro.
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
            "strokeColor": CINZA, "backgroundColor": "transparent",
            "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
            "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
            "roundness": None, "seed": _seed(self._n), "version": 1,
            "versionNonce": _seed(self._n), "isDeleted": False,
            "boundElements": [], "updated": 1, "link": None, "locked": False,
            "fontSize": 16, "fontFamily": 2, "text": rotulo,
            "textAlign": "center", "verticalAlign": "top",
            "containerId": None, "originalText": rotulo, "lineHeight": 1.25,
            "baseline": 14,
        })
        return {"x": x, "y": y, "w": ICON, "h": ICON, "cx": cx, "cy": cy}

    # borda de um no, para ancorar setas
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
    def _rotulo_blindado(self, cx, cy, texto, cor=CINZA, fontSize=13, w=220,
                          h=22, align="center"):
        """L18: retangulo branco opaco ATRAS do texto do rotulo de seta —
        a seta tem roundness type 2 (curva bezier), nenhum verificador por
        segmentos retos modela o bojo da curva, entao blindamos sempre."""
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
             tracejada=False, strokeWidth=2, frac=0.5, label_w=220,
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

    def fan_out(self, a, lado_a, destinos, rotulo=None, cor=CINZA,
                tracejada=False, strokeWidth=2, label_dx=24, label_dy=-30,
                label_w=200):
        """1 origem -> N destinos (L11): todas as setas partem do MESMO
        ponto; rotulo unico nesse ponto de divisao."""
        px, py = self.borda(a, lado_a)
        for b, lado_b in destinos:
            x2, y2 = self.borda(b, lado_b)
            self._arrow(px, py, x2, y2, cor, tracejada, strokeWidth)
        if rotulo:
            self._rotulo_blindado(px + label_dx, py + label_dy, rotulo,
                                   cor=cor, w=label_w, align="left")

    # -------------------------------------------------------- texto/estrutura
    def titulo(self, texto, x, y, w=1200):
        self.elements.append({
            "id": self._id("title"), "type": "text", "x": x, "y": y,
            "width": w, "height": 30, "angle": 0, "strokeColor": FIAP_MAGENTA,
            "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 1, "strokeStyle": "solid", "roughness": 1,
            "opacity": 100, "groupIds": [], "frameId": None, "roundness": None,
            "seed": _seed(self._n), "version": 1, "versionNonce": _seed(self._n),
            "isDeleted": False, "boundElements": [], "updated": 1, "link": None,
            "locked": False, "fontSize": 22, "fontFamily": 2, "text": texto,
            "textAlign": "left", "verticalAlign": "top", "containerId": None,
            "originalText": texto, "lineHeight": 1.25, "baseline": 19,
        })

    def nota(self, texto, x, y, w=900, cor=CINZA, fontSize=14, align="left"):
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
# Layout — 3 grupos verticais (esquerda/central/direita) por coordenada.
# ---------------------------------------------------------------------------

# colunas
xL, xC, xR1, xR2, xR3 = 200, 600, 1100, 1400, 1700
# linhas — passo de 350px (era 250): com icone de 80 + rotulo de 2 linhas (~40)
# sobram ~230px de respiro entre faixas, a mesma densidade por no dos diagramas
# aprovados (~190k px2/no). O aperto vertical era a causa raiz do "difícil de ler".
yA, yB, yC, yD = 190, 540, 890, 1240


def montar(passe2: bool):
    d = Diagrama()

    d.titulo("Lab 04.2 · Bora Fibra — releases V1/V2 do SLM no SageMaker, "
              "sem AWS keys no GitHub", 60, 30, w=1600)
    d.nota("Quality roda sem credencial nenhuma; deploy roda com papel IAM "
           "assumido via IMDSv2.", 60, 66, w=1600)

    # ---- nos ----
    # escudo_w: rotulo cruzado por seta (medido por pixel na cena com x sem
    # setas). Valor = tinta da linha mais longa em Helvetica 16 + 12px.
    # lbl_w acima do padrao: a 2a linha mede 254px em Helvetica 16 e vazaria
    # a caixa de 230 (o texto e centrado, entao vazaria dos dois lados)
    hf = d.no("huggingface", "Hugging Face Hub\n(repositório público, revisão pinada)", xL, yA,
              lbl_w=270, escudo_w=266)
    cs = d.no("github", "Codespaces\n(dev + deploy V1 manual)", xL, yB, escudo_w=195)
    repo = d.no("github", "GitHub repository\n(workflows + manifests V1/V2)", xL, yC)

    ghrun = d.no("github", "GitHub-hosted runner\nQUALITY / SEM AWS", xC, yB)
    # a credencial vive no rotulo, nao em no proprio: o LabInstanceProfile e
    # atributo da instancia, e desenha-lo como servico criava um canal de cor
    # inteiro (2 setas azuis + 2 rotulos) no corredor central.
    ec2run = d.no("ec2", "EC2 self-hosted runner\nDEPLOY · LabRole (IMDSv2)", xC, yC,
                  lbl_w=250)

    s3 = d.no("s3", "S3 artifacts\n(prefixo imutável por revisão HF)", xR1, yA, lbl_w=245,
              escudo_w=244)

    smv1 = d.no("sagemaker", "SageMaker Model V1\n(GGUF Q4_0 · ml.m5.xlarge)", xR2, yB,
                escudo_w=218)
    smv2 = d.no("sagemaker", "SageMaker Model V2\n(GGUF Q4_K_M · ml.m5.xlarge)", xR3, yB,
                escudo_w=242)
    epv1 = d.no("sagemaker", "Endpoint V1\n(InService, capacidade fixa 1)", xR2, yC,
                escudo_w=223)
    epv2 = d.no("sagemaker", "Endpoint V2\n(InService, min 1 / max 2)", xR3, yC,
                escudo_w=194)

    cw = d.no("cloudwatch", "CloudWatch\n(dashboard fiap-mlops-slm-<suffix>)", xR2, yD,
              lbl_w=270)
    aas = d.no("autoscaling", "Application Auto Scaling\n(V2: min 1 / max 2)", xR3, yD)

    if passe2:
        # ---- faixa esquerda ----
        # rotulo deslocado para a esquerda da coluna: a diagonal hf->ec2run
        # cruza essa altura por volta de x=330 e riscaria o texto.
        d.liga(hf, "B", cs, "T", "download modelo (pinado, SHA-256)",
               label_dx=-30)
        d.liga(cs, "B", repo, "T", "commit/push")

        # ---- esquerda -> central ----
        d.liga(repo, "D", ghrun, "E", "dispara quality (PR/push)", strokeWidth=2)
        d.liga(repo, "D", ec2run, "E",
               "dispara deploy manual (owner/master)", strokeWidth=2,
               label_w=230)
        # sem rotulo de proposito: o rotulo antigo tinha de ser deslocado ~140px
        # para fugir de colisao e passava a apontar para seta nenhuma. A seta irma
        # hf->cs, logo acima, ja nomeia a acao (e a mencao unica de SHA-256).
        d.liga(hf, "D", ec2run, "T")

        # ---- esquerda/central -> S3 ----
        d.liga(cs, "D", s3, "E", "sobe modelo V1",
               frac=0.55, label_dy=-30)
        d.liga(ec2run, "D", s3, "B", "sobe modelo V2",
               frac=0.6, label_dx=30, label_dy=-14)

        # ---- S3 -> SageMaker (fan-out, L11) ----
        # label_dx afasta a caixa do rotulo do proprio icone do S3 (x 1060..1140):
        # `_rotulo_blindado` centra a caixa em (px+dx), entao dx<140 invadia o icone.
        d.fan_out(s3, "D", [(smv1, "E"), (smv2, "E")], "referencia artefato",
                  label_dx=150, label_dy=-34)

        # ---- SageMaker -> Endpoint (setas irmas: um rotulo so, como no 04.1) ----
        d.liga(smv1, "B", epv1, "T", "publica")
        d.liga(smv2, "B", epv2, "T")

        # ---- Endpoint -> CloudWatch (observabilidade, tracejado cinza) ----
        # V2 chega pela borda DIREITA do CloudWatch: mirando a borda esquerda, a
        # diagonal terminava a ~3px da ponta da vertical do V1 (dois arrowheads
        # colados). Pela direita a separacao volta a ~56px. Rotulo unico na vertical.
        d.liga(epv1, "B", cw, "T", "envia métricas nativas", tracejada=True)
        d.liga(epv2, "B", cw, "D", tracejada=True)

        # ---- Auto Scaling -> Endpoint V2 (só V2; V1 fica sem seta = capacidade fixa) ----
        d.liga(aas, "T", epv2, "B", "ajusta capacidade (min 1 / max 2)")

    # legenda de traco (rodape). A ficha tecnica do runner saiu: e conteudo de
    # README. Sem o canal azul, o tracejado volta a ter um unico significado.
    d.nota("Sólido = release  ·  Tracejado = observabilidade",
           60, 1370, w=1700, fontSize=14)

    return d


if __name__ == "__main__":
    passe2 = "--passe1" not in sys.argv
    d = montar(passe2)
    d.salvar(os.path.join(DIR, "arquitetura.excalidraw"))
