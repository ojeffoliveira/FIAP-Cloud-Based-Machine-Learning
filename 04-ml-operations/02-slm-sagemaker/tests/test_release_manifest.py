"""Testes de contrato dos manifests de release do SLM (`model/releases/*.yaml`).

Cada release (V1 manual, V2 pelo pipeline) precisa satisfazer o contrato da
spec (`02_SPEC_LAB04_2_SLM_CICD.md` §5) para que `scripts/model_sync.py` e o
Terraform possam confiar nos campos sem validação redundante em runtime.
Revisão do Hugging Face imutável e SHA-256 conferido são a base da garantia
de supply chain do modelo — ver `05_GITHUB_ACTIONS_SECURITY.md` §11.
"""

import pathlib
import re

import pytest
import yaml

LAB_ROOT = pathlib.Path(__file__).resolve().parents[1]
RELEASES_DIR = LAB_ROOT / "model" / "releases"
V1 = RELEASES_DIR / "v1.yaml"
V2 = RELEASES_DIR / "v2.yaml"

REQUIRED_TOP_LEVEL_FIELDS = {
    "release",
    "model",
    "instance_type",
    "max_context",
    "max_output_tokens",
    "temperature",
    "prompt_contract_version",
}
REQUIRED_MODEL_FIELDS = {
    "source",
    "repo",
    "revision",
    "filename",
    "sha256",
    "size_bytes",
    "quantization",
    "license",
}
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# Instâncias de inferência CPU permitidas pela spec/Academy para o SLM
# (ver 02_SPEC_LAB04_2_SLM_CICD.md §6.4/§7 — nunca GPU, foco didático em CPU
# DLC dentro do budget do Learner Lab).
ALLOWED_INSTANCE_TYPES = {"ml.m5.xlarge", "ml.m5.large", "ml.m5.2xlarge"}
GPU_INSTANCE_PREFIXES = ("ml.g", "ml.p")


def _load(path: pathlib.Path) -> dict:
    if not path.exists():
        pytest.fail(f"manifest de release obrigatório ausente: {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


parametrize_releases = pytest.mark.parametrize(
    "manifest_path,esperado_release,esperada_quantizacao",
    [
        pytest.param(V1, "v1", "Q4_0", id="v1"),
        pytest.param(V2, "v2", "Q4_K_M", id="v2"),
    ],
)


@parametrize_releases
def test_campos_obrigatorios_presentes(
    manifest_path, esperado_release, esperada_quantizacao
):
    """Reprova se qualquer campo obrigatório da spec §5 estiver ausente —
    scripts/model_sync.py e o Terraform leem estes campos sem fallback."""
    doc = _load(manifest_path)
    faltando_topo = REQUIRED_TOP_LEVEL_FIELDS - doc.keys()
    assert not faltando_topo, (
        f"{manifest_path.name}: campos de topo ausentes: {faltando_topo}"
    )
    faltando_model = REQUIRED_MODEL_FIELDS - doc["model"].keys()
    assert not faltando_model, (
        f"{manifest_path.name}: campos de model ausentes: {faltando_model}"
    )


@parametrize_releases
def test_release_e_v1_ou_v2(manifest_path, esperado_release, esperada_quantizacao):
    """Reprova se `release` não for exatamente o valor esperado do arquivo —
    evita v1.yaml/v2.yaml trocados ou copiados um do outro sem editar."""
    doc = _load(manifest_path)
    assert doc["release"] == esperado_release, (
        f"{manifest_path.name}: release deveria ser {esperado_release!r}, "
        f"encontrado {doc['release']!r}"
    )


@parametrize_releases
def test_revision_e_sha_imutavel_de_40_chars(
    manifest_path, esperado_release, esperada_quantizacao
):
    """Reprova se `revision` não for um SHA de commit de 40 hex (nunca
    `main`) — `main` pode avançar entre a preparação da aula e a execução
    em sala, quebrando a reprodutibilidade do release."""
    doc = _load(manifest_path)
    revision = doc["model"]["revision"]
    assert revision != "main", f"{manifest_path.name}: revision não pode ser 'main'"
    assert HEX40_RE.fullmatch(revision), (
        f"{manifest_path.name}: revision deve ser SHA de commit de 40 hex, "
        f"encontrado {revision!r}"
    )


@parametrize_releases
def test_sha256_hex_64_chars(manifest_path, esperado_release, esperada_quantizacao):
    """Reprova se `sha256` não tiver o formato de um digest SHA-256 —
    scripts/model_sync.py falha duro em mismatch, mas só se o valor
    esperado já estiver bem formado no manifest."""
    doc = _load(manifest_path)
    sha256 = doc["model"]["sha256"]
    assert HEX64_RE.fullmatch(sha256), (
        f"{manifest_path.name}: sha256 deve ser hex de 64 chars, encontrado {sha256!r}"
    )


@parametrize_releases
def test_quantization_esperada(manifest_path, esperado_release, esperada_quantizacao):
    """Reprova se a quantização não for exatamente Q4_0 (V1) ou Q4_K_M
    (V2) — é a única diferença intencional de artefato entre as releases,
    conforme spec §5."""
    doc = _load(manifest_path)
    assert doc["model"]["quantization"] == esperada_quantizacao, (
        f"{manifest_path.name}: quantization deveria ser "
        f"{esperada_quantizacao!r}, encontrado {doc['model']['quantization']!r}"
    )


@parametrize_releases
def test_license_apache_2_0(manifest_path, esperado_release, esperada_quantizacao):
    """Reprova se a licença não for Apache-2.0 — é a licença do modelo
    Qwen2.5 e condição para uso didático sem restrição adicional."""
    doc = _load(manifest_path)
    assert doc["model"]["license"] == "Apache-2.0", (
        f"{manifest_path.name}: license deveria ser 'Apache-2.0', "
        f"encontrado {doc['model']['license']!r}"
    )


@parametrize_releases
def test_instance_type_permitida_e_nunca_gpu(
    manifest_path, esperado_release, esperada_quantizacao
):
    """Reprova instância GPU (ml.g*/ml.p*) ou fora da lista permitida — o
    Academy não tem GPU disponível e o lab é deliberadamente CPU-only."""
    doc = _load(manifest_path)
    instance_type = doc["instance_type"]
    assert not instance_type.startswith(GPU_INSTANCE_PREFIXES), (
        f"{manifest_path.name}: instance_type não pode ser GPU, "
        f"encontrado {instance_type!r}"
    )
    assert instance_type in ALLOWED_INSTANCE_TYPES, (
        f"{manifest_path.name}: instance_type {instance_type!r} fora da "
        f"lista permitida do Academy: {ALLOWED_INSTANCE_TYPES}"
    )


@parametrize_releases
def test_max_output_tokens_limitado(
    manifest_path, esperado_release, esperada_quantizacao
):
    """Reprova max_output_tokens > 64 — o contrato de saída do SLM é
    2-3 frases curtas (spec §3), não geração longa; also mantém a latência
    dentro do budget de demonstração em CPU."""
    doc = _load(manifest_path)
    assert doc["max_output_tokens"] <= 64, (
        f"{manifest_path.name}: max_output_tokens deve ser <= 64, "
        f"encontrado {doc['max_output_tokens']!r}"
    )


@parametrize_releases
def test_temperature_zero_para_smoke_e_eval(
    manifest_path, esperado_release, esperada_quantizacao
):
    """Reprova temperature != 0 — sem determinismo, o gate de evaluation
    não é reproduzível entre execuções (spec §7)."""
    doc = _load(manifest_path)
    assert doc["temperature"] == 0, (
        f"{manifest_path.name}: temperature deve ser 0 para smoke/eval, "
        f"encontrado {doc['temperature']!r}"
    )


@parametrize_releases
def test_prompt_contract_version_presente(
    manifest_path, esperado_release, esperada_quantizacao
):
    """Reprova ausência de prompt_contract_version — é o campo que isola
    mudança de prompt contract de mudança de artefato/quantização ao
    comparar V1 x V2."""
    doc = _load(manifest_path)
    assert doc.get("prompt_contract_version"), (
        f"{manifest_path.name}: prompt_contract_version ausente ou vazio"
    )


@parametrize_releases
def test_filename_coerente_com_quantizacao(
    manifest_path, esperado_release, esperada_quantizacao
):
    """Reprova filename que não menciona a quantização declarada — evita
    servir o arquivo Q4_0 sob um manifest que diz Q4_K_M (ou vice-versa)."""
    doc = _load(manifest_path)
    filename = doc["model"]["filename"].lower()
    quantizacao_no_nome = esperada_quantizacao.lower().replace("_", "_")
    assert quantizacao_no_nome in filename or quantizacao_no_nome.replace(
        "_", ""
    ) in filename.replace("_", ""), (
        f"{manifest_path.name}: filename {filename!r} não é coerente com "
        f"quantization {esperada_quantizacao!r}"
    )


def test_prompt_contract_version_identica_entre_v1_e_v2():
    """Reprova se V1 e V2 tiverem prompt_contract_version diferente sem
    justificativa — a spec §5 exige isolar quantização/artefato como única
    variável ao comparar V1 x V2; se o prompt contract também mudasse, a
    comparação deixaria de ser justa."""
    v1 = _load(V1)
    v2 = _load(V2)
    assert v1["prompt_contract_version"] == v2["prompt_contract_version"], (
        "prompt_contract_version diverge entre v1.yaml e v2.yaml: "
        f"{v1['prompt_contract_version']!r} != {v2['prompt_contract_version']!r}"
    )
