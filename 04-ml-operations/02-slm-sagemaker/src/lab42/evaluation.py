"""Gate generativo do Lab 04.2 (spec §8) — nunca comparação de texto exato.

Cada caso de `eval/cases.yaml` é validado por uma bateria de checagens
independentes e explicáveis (protocolo, schema, tamanho, idioma, PII/valor
inventado, presença de termo esperado, envelope de latência medido). Nenhuma
delas usa modelo de linguagem nem biblioteca pesada de NLP — são heurísticas
determinísticas, documentadas com o método e as limitações, porque é isso que
o aluno precisa conseguir explicar depois (e depurar quando o SLM de 0,5B
responder algo estranho).
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import yaml

from . import aws, inference

CASES_PATH = aws.LAB_ROOT / "eval" / "cases.yaml"

# Teto de caracteres da resposta. Não é o limite real (o servidor já corta em
# `max_tokens=64`, aplicado pelo próprio llama-server) — é uma segunda trava,
# independente do runtime, para o gate reprovar sozinho se um release futuro
# esquecer de propagar `max_tokens`. Aproximação documentada: ~9 caracteres
# por token em português (subword tokenizers costumam partir palavras
# acentuadas em mais de um token), com 15% de margem sobre 64 tokens.
LENGTH_CHAR_CEILING = 700

_REQUIRED_CASE_KEYS = ("id", "contexto", "checks")


# --------------------------------------------------------------------------- #
# eval/cases.yaml
# --------------------------------------------------------------------------- #


@functools.lru_cache(maxsize=1)
def load_cases() -> list[dict[str, Any]]:
    """Lê e valida minimamente `eval/cases.yaml` (schema §8 da spec)."""
    if not CASES_PATH.exists():
        raise aws.LabError(f"{CASES_PATH} não existe.")
    casos = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8")) or []
    if not isinstance(casos, list) or not (6 <= len(casos) <= 10):
        raise aws.LabError(
            f"{CASES_PATH} precisa ter entre 6 e 10 casos (spec §8); encontrado {len(casos)}."
        )
    ids_vistos: set[str] = set()
    for caso in casos:
        faltando = [chave for chave in _REQUIRED_CASE_KEYS if chave not in caso]
        if faltando:
            raise aws.LabError(f"caso {caso!r} sem chave(s) obrigatória(s): {faltando}")
        if caso["id"] in ids_vistos:
            raise aws.LabError(f"id de caso duplicado em {CASES_PATH}: {caso['id']!r}")
        ids_vistos.add(caso["id"])
    return casos


def get_case(case_id: str) -> dict[str, Any]:
    for caso in load_cases():
        if caso["id"] == case_id:
            return caso
    disponiveis = ", ".join(c["id"] for c in load_cases())
    raise aws.LabError(
        f"caso {case_id!r} não existe em {CASES_PATH}. Disponíveis: {disponiveis}"
    )


# --------------------------------------------------------------------------- #
# Heurística de "português plausível" — método explícito, sem lib de NLP.
# --------------------------------------------------------------------------- #

# ~90 palavras de função (artigos, preposições, conjunções, pronomes) do
# português — sinal mais estável de idioma do que vocabulário de conteúdo,
# porque aparece em qualquer frase gramatical, independente do assunto.
_PT_STOPWORDS = frozenset(
    [
        "de",
        "a",
        "o",
        "que",
        "e",
        "do",
        "da",
        "em",
        "um",
        "uma",
        "para",
        "com",
        "não",
        "na",
        "no",
        "se",
        "por",
        "mais",
        "as",
        "dos",
        "como",
        "mas",
        "ao",
        "ele",
        "das",
        "seu",
        "sua",
        "ou",
        "quando",
        "muito",
        "nos",
        "já",
        "eu",
        "também",
        "só",
        "pelo",
        "pela",
        "até",
        "isso",
        "ela",
        "entre",
        "depois",
        "sem",
        "mesmo",
        "aos",
        "seus",
        "quem",
        "nas",
        "me",
        "esse",
        "eles",
        "essa",
        "num",
        "nem",
        "suas",
        "meu",
        "minha",
        "numa",
        "pelos",
        "elas",
        "qual",
        "nós",
        "lhe",
        "deles",
        "essas",
        "esses",
        "pelas",
        "este",
        "dele",
        "tu",
        "te",
        "vocês",
        "vos",
        "lhes",
        "meus",
        "minhas",
        "teu",
        "tua",
        "teus",
        "tuas",
        "nosso",
        "nossa",
        "nossos",
        "nossas",
        "dela",
        "delas",
        "esta",
        "estes",
        "estas",
        "aquele",
        "aquela",
        "aqueles",
        "aquelas",
        "isto",
        "aquilo",
        "é",
        "são",
        "foi",
        "ser",
        "está",
        "estão",
        "pode",
        "podem",
        "deve",
        "devem",
        "cliente",
        "risco",
        "ação",
        "sugere",
        "explica",
        "atendente",
        "contexto",
    ]
)

# Palavras funcionais de inglês comuns — usadas só como contrapeso: se a
# fração delas superar a de stopwords PT, o texto provavelmente não é
# português mesmo que tenha 1-2 acentos por acidente (nome próprio, etc.).
_EN_TELLS = frozenset(
    [
        "the",
        "and",
        "you",
        "is",
        "are",
        "this",
        "that",
        "with",
        "for",
        "your",
        "not",
        "have",
        "will",
        "can",
        "should",
    ]
)

_PT_ACCENTED_CHARS = frozenset("áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ")

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def looks_like_ptbr(texto: str) -> tuple[bool, dict[str, Any]]:
    """Heurística explicável de "é português plausível" — não é detector de idioma real.

    Método:
    1. tokeniza em palavras minúsculas (regex de letras Unicode, sem dígitos);
    2. conta quantas dessas palavras estão na lista fixa de stopwords PT-BR
       acima;
    3. conta caracteres tipicamente portugueses (acentos + `ç`);
    4. aprova se a fração de stopwords PT for >= 0.15, OU se houver pelo
       menos 1 caractere acentuado E pelo menos 1 stopword PT (a segunda
       condição evita reprovar textos curtos, onde a fração de uma única
       palavra já teria muito peso);
    5. reprova, mesmo assim, se a contagem de "tells" de inglês superar a de
       stopwords PT.

    Limitações conhecidas, documentadas (não escondidas):
    - não distingue português do Brasil de Portugal;
    - textos com menos de ~5 palavras têm alta variância nesta heurística;
    - aprova português gramaticalmente errado mas lexicalmente português —
      comportamento correto para um SLM de 0,5B parâmetros, que não deve ser
      julgado pela mesma régua de um modelo de produção maior;
    - não é um classificador estatístico (sem `langdetect`/`fasttext`/etc.);
      suficiente para o gate deste lab, não para uso multilíngue em produção.
    """
    palavras = _WORD_RE.findall(texto.lower())
    total = len(palavras)
    if total == 0:
        return False, {"motivo": "texto vazio", "total_palavras": 0}

    stop_count = sum(1 for p in palavras if p in _PT_STOPWORDS)
    en_count = sum(1 for p in palavras if p in _EN_TELLS)
    acentos = sum(1 for c in texto if c in _PT_ACCENTED_CHARS)
    fracao_stop = stop_count / total

    aprovado = (fracao_stop >= 0.15) or (acentos >= 1 and stop_count >= 1)
    if en_count > stop_count:
        aprovado = False

    return aprovado, {
        "total_palavras": total,
        "stopwords_pt_encontradas": stop_count,
        "fracao_stopwords_pt": round(fracao_stop, 3),
        "caracteres_acentuados": acentos,
        "tells_ingles_encontrados": en_count,
    }


# --------------------------------------------------------------------------- #
# PII inventada / valor monetário ou percentual não fornecido
# --------------------------------------------------------------------------- #

_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\b(?:\+?55\s?)?\(?\d{2}\)?[\s-]?9?\d{4}[\s-]?\d{4}\b")
_MONEY_OR_PERCENT_RE = re.compile(r"r\$\s?\d|\b\d+([.,]\d+)?\s?%|\bdesconto\s+de\s+\d")

# Termos conhecidos do `nao_deve_inventar` (spec §8) recebem um checador mais
# preciso que regex/substring simples; qualquer termo fora desta lista cai no
# fallback de substring normalizado (sem acento, minúsculo) em
# `check_nao_inventou`.
_TERMO_CHECADORES: dict[str, Any] = {
    "cpf": lambda norm, txt: bool(_CPF_RE.search(txt)) or "cpf" in norm,
    "telefone": lambda norm, txt: (
        bool(_PHONE_RE.search(txt)) or "telefone" in norm or "whatsapp" in norm
    ),
    "endereco": lambda norm, txt: (
        "endereco" in norm or "rua " in norm or "avenida " in norm
    ),
    "e-mail": lambda norm, txt: bool(_EMAIL_RE.search(txt)) or "email" in norm,
    "email": lambda norm, txt: bool(_EMAIL_RE.search(txt)) or "email" in norm,
    "valor de desconto especifico": lambda norm, txt: bool(
        _MONEY_OR_PERCENT_RE.search(txt)
    ),
}


def _normalizar(texto: str) -> str:
    """Minúsculo e sem acento — para comparar termo do case (PT-BR acentuado) com o texto."""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def check_nao_inventou(texto: str, termos_proibidos: list[str]) -> list[str]:
    """Devolve os termos de `nao_deve_inventar` (case) que aparecem no texto — heurística.

    Limitações: substring normalizado não detecta paráfrase (ex.: o modelo
    poderia descrever um desconto sem usar "%", "R$" nem a palavra
    "desconto" — não capturado); o regex de telefone pode falso-positivar em
    sequências longas de dígitos que não são telefone. Preferimos o
    falso-positivo (reprovar um caso que na verdade estava certo) ao
    falso-negativo (deixar passar PII/valor inventado), coerente com a regra
    dura do lab de nunca inventar dado sensível.
    """
    texto_lower = texto.lower()
    norm = _normalizar(texto)
    violacoes = []
    for termo in termos_proibidos:
        chave = _normalizar(termo)
        checador = _TERMO_CHECADORES.get(chave)
        encontrado = checador(norm, texto_lower) if checador else chave in norm
        if encontrado:
            violacoes.append(termo)
    return violacoes


def mentions_any(texto: str, termos: list[str]) -> bool:
    """Verdadeiro se QUALQUER UM dos termos aparecer no texto (substring normalizado).

    `deve_mencionar_algum` é satisfeito por um único termo — não é uma lista
    de obrigatórios. Limitação: não pega sinônimo/flexão fora da lista do
    case (ex.: "atender" não bate com termo "atendimento") — por isso cada
    case em `eval/cases.yaml` lista vários termos plausíveis.
    """
    norm = _normalizar(texto)
    return any(_normalizar(termo) in norm for termo in termos)


def is_valid_utf8_text(texto: str) -> bool:
    """Confere que o texto é UTF-8 válido de ponta a ponta e sem caractere de substituição.

    `json.loads` já garante que o corpo era UTF-8 decodificável (senão a
    própria chamada em `inference.invoke` teria falhado antes de chegar
    aqui) — este check é uma segunda confirmação, e pega o sintoma mais comum
    de um decode "silenciosamente errado" no meio do caminho: o caractere de
    substituição U+FFFD.
    """
    if "�" in texto:
        return False
    try:
        texto.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


# --------------------------------------------------------------------------- #
# Envelope de latência congelado por `scripts/benchmark.py`
# --------------------------------------------------------------------------- #


def load_envelope_ms() -> dict[str, Any] | None:
    """Lê `latency_envelope_ms` de `.generated/runtime.json`, ou `None` se ainda não rodou.

    Nunca inventa um valor: se `make benchmark-v1` (ou o passo equivalente do
    workflow V2) ainda não rodou nesta máquina, os casos deste `evaluate.py`
    simplesmente não reprovam por latência — ficam marcados como
    "envelope pendente" no detalhe do veredito.
    """
    return inference.read_generated_runtime().get("latency_envelope_ms")


# --------------------------------------------------------------------------- #
# Veredito por caso e agregado
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CaseVerdict:
    id: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    latency_s: float | None = None
    text: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "passed": self.passed,
            "reasons": self.reasons,
            "latency_s": self.latency_s,
            "texto": self.text,
            "detalhes": self.details,
        }


def evaluate_case(
    release: str,
    caso: dict[str, Any],
    *,
    endpoint_name: str | None = None,
    envelope_ms: dict[str, Any] | None = None,
) -> CaseVerdict:
    """Roda o gate generativo completo (spec §8) para um único caso."""
    caso_id = caso["id"]
    contexto = caso["contexto"]
    checks = caso.get("checks", {})
    razoes: list[str] = []

    try:
        resultado = inference.invoke_release(
            release, contexto, endpoint_name=endpoint_name
        )
    except inference.InferenceProtocolError as exc:
        return CaseVerdict(caso_id, False, [f"erro_de_protocolo: {exc}"])
    except inference.InferenceContentError as exc:
        return CaseVerdict(caso_id, False, [f"erro_de_conteudo: {exc}"])

    texto = resultado.text or ""
    detalhes: dict[str, Any] = {
        "finish_reason": resultado.finish_reason,
        "completion_tokens": resultado.completion_tokens,
        "prompt_tokens": resultado.prompt_tokens,
    }

    if not texto.strip():
        razoes.append("texto_vazio")
    if not is_valid_utf8_text(texto):
        razoes.append("utf8_invalido")
    if len(texto) > LENGTH_CHAR_CEILING:
        razoes.append(
            f"acima_do_limite_de_tamanho:{len(texto)}>{LENGTH_CHAR_CEILING}chars"
        )

    ptbr_ok, ptbr_detalhes = looks_like_ptbr(texto)
    detalhes["portugues"] = ptbr_detalhes
    if checks.get("idioma", "pt-BR") == "pt-BR" and not ptbr_ok:
        razoes.append("nao_parece_portugues")

    proibidos = checks.get("nao_deve_inventar", [])
    violacoes = check_nao_inventou(texto, proibidos)
    detalhes["violacoes_pii_ou_valor"] = violacoes
    if violacoes:
        razoes.append(f"citou_termo_proibido:{violacoes}")

    esperados = checks.get("deve_mencionar_algum", [])
    if esperados and not mentions_any(texto, esperados):
        razoes.append(f"nenhum_termo_esperado_presente:{esperados}")

    if envelope_ms:
        p95_max = envelope_ms.get("p95_max")
        latencia_ms = resultado.latency_s * 1000
        detalhes["envelope_ms"] = envelope_ms
        detalhes["latencia_ms"] = round(latencia_ms, 1)
        if p95_max is not None and latencia_ms > p95_max:
            razoes.append(f"latencia_acima_do_envelope:{latencia_ms:.0f}ms>{p95_max}ms")
    else:
        detalhes["envelope_ms"] = None
        detalhes["observacao_envelope"] = (
            "envelope de latência ainda não congelado nesta máquina (rode "
            "'make benchmark-v1' antes de 'make quality') — este caso não "
            "reprova por latência nesta execução."
        )

    return CaseVerdict(
        id=caso_id,
        passed=not razoes,
        reasons=razoes,
        latency_s=resultado.latency_s,
        text=texto,
        details=detalhes,
    )


def run_evaluation(release: str, *, endpoint_name: str | None = None) -> dict[str, Any]:
    """Roda o gate contra todos os casos de `eval/cases.yaml` e agrega o resultado.

    Contrato de saída pensado para `evidence.STAGE_SCHEMA["evaluation-v1.json"]`
    (`cases_total`, `cases_passed`, `pii_violations`) — os demais campos são
    adicionais, não removem nenhum dos exigidos.
    """
    casos = load_cases()
    envelope = load_envelope_ms()
    nome_endpoint = endpoint_name or inference.resolve_endpoint_name(release)
    aws.log(
        f"avaliando {len(casos)} casos contra {nome_endpoint} (release={release})..."
    )

    veredictos = []
    for caso in casos:
        aws.log(f"  caso {caso['id']}...")
        veredictos.append(
            evaluate_case(
                release, caso, endpoint_name=nome_endpoint, envelope_ms=envelope
            )
        )

    total = len(veredictos)
    aprovados = sum(1 for v in veredictos if v.passed)
    pii_violations = sum(
        1
        for v in veredictos
        if any(r.startswith("citou_termo_proibido") for r in v.reasons)
    )

    return {
        "release": release,
        "endpoint_name": nome_endpoint,
        "cases_total": total,
        "cases_passed": aprovados,
        "pii_violations": pii_violations,
        "envelope_ms": envelope,
        "metodo_idioma": "heuristica_stopwords_pt_br (ver src/lab42/evaluation.py:looks_like_ptbr)",
        "casos": [v.to_dict() for v in veredictos],
    }
