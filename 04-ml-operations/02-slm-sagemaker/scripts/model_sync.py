#!/usr/bin/env python3
"""Publica a release do SLM (V1 Q4_0 / V2 Q4_K_M) no bucket de artifacts.

Uso:
  python scripts/model_sync.py --release v1
  python scripts/model_sync.py --release v2
  python scripts/model_sync.py --release v2 --action recommend --reason "gates da v2 passaram"

Convenção do laboratório: stdout carrega só o resultado (JSON), stderr carrega
progresso — quem consome este script (Makefile, evidence.py, um humano) pode
capturar `python scripts/model_sync.py --release v1 > out.json` sem ruído.

Idempotência: antes de baixar qualquer coisa, faz head-object na key de
destino (imutável por release/revisão, layout D7 — um `.gguf` por prefixo).
Se já existe com o SHA-256 correto, reusa e não baixa/reenvia nada — importa
durante a aula, onde re-subir ~430-490 MB a cada execução do pipeline seria
desperdício de tempo e de crédito AWS.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab42 import aws, evidence, hf_release
from lab42.aws import LabError, emit, log


def _bucket(cfg: dict, override: str | None) -> str:
    if override:
        return override
    return aws.resource_name(cfg["naming"]["bucket_artifacts"])


def cmd_sync(release: str, bucket_override: str | None) -> dict:
    cfg = aws.load_config()
    manifest = hf_release.load_manifest(hf_release.manifest_path(release))
    if manifest["release"] != release:
        raise LabError(
            f"model/releases/{release}.yaml declara release={manifest['release']!r}, esperado {release!r}"
        )

    model = manifest["model"]
    bucket = _bucket(cfg, bucket_override)
    key = hf_release.s3_key_for(manifest)

    log(
        f"release={release} repo={model['repo']} revision={model['revision']} filename={model['filename']}"
    )
    log(f"destino: s3://{bucket}/{key}")

    reused = hf_release.head_object_matches(
        bucket, key, expected_sha256=model["sha256"], expected_size=model["size_bytes"]
    )

    local_path = None
    try:
        if reused:
            log(
                "objeto já existe no S3 com sha256/tamanho corretos — pulando download e upload (idempotente)"
            )
        else:
            local_path = hf_release.download_and_verify(manifest)
            hf_release.upload_with_metadata(
                local_path,
                bucket,
                key,
                metadata={
                    "sha256": model["sha256"],
                    "revision": model["revision"],
                    "release": release,
                    "quantization": model["quantization"],
                },
            )
    finally:
        if local_path is not None:
            local_path.unlink(missing_ok=True)
            try:
                local_path.parent.rmdir()
            except OSError:
                pass  # diretório não vazio ou já removido — não é fatal aqui.

    s3_uri = f"s3://{bucket}/{key}"
    payload = {
        "release": release,
        "repo": model["repo"],
        "revision": model["revision"],
        "filename": model["filename"],
        "sha256": model["sha256"],
        "size_bytes": model["size_bytes"],
        "quantization": model["quantization"],
        "license": model["license"],
        "s3_bucket": bucket,
        "s3_key": key,
        "s3_uri": s3_uri,
        "reused_existing_object": reused,
    }
    hf_release.write_s3_metadata_json(bucket, release, payload)
    evidence.write(f"model-{release}.json", payload)
    return payload


def cmd_recommend(
    release: str, bucket_override: str | None, reason: str | None
) -> dict:
    if not reason:
        raise LabError(
            "--action recommend exige --reason (por que esta release está sendo recomendada)"
        )
    cfg = aws.load_config()
    bucket = _bucket(cfg, bucket_override)
    manifest = hf_release.load_manifest(hf_release.manifest_path(release))
    if manifest["release"] != release:
        raise LabError(
            f"model/releases/{release}.yaml declara release={manifest['release']!r}, esperado {release!r}"
        )
    existente = hf_release.read_s3_metadata_json(bucket, release)
    if existente is None:
        raise LabError(
            f"metadata/{release}.json não existe em s3://{bucket}/ — rode "
            f"`python scripts/model_sync.py --release {release}` antes de recomendar."
        )
    return hf_release.write_recommended_release(bucket, release, reason=reason)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", choices=["v1", "v2"], required=True)
    parser.add_argument("--action", choices=["sync", "recommend"], default="sync")
    parser.add_argument(
        "--bucket",
        default=None,
        help="Override do nome do bucket de artifacts (normalmente derivado de config/lab.yaml)",
    )
    parser.add_argument(
        "--reason",
        default=None,
        help="Motivo da promoção (obrigatório com --action recommend)",
    )
    args = parser.parse_args()

    try:
        if args.action == "sync":
            resultado = cmd_sync(args.release, args.bucket)
        else:
            resultado = cmd_recommend(args.release, args.bucket, args.reason)
    except LabError as exc:
        log(f"[FAIL] {exc}")
        return 1

    log(f"[PASS] release={args.release} action={args.action}")
    emit(resultado)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
