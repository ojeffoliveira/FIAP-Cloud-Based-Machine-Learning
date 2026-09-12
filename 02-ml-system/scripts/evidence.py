#!/usr/bin/env python3
"""Monta o pacote de evidências.

A tese central da primeira aula é que "correto" é uma cadeia de evidências, não
uma métrica isolada. Este script materializa essa cadeia num arquivo: qual dado
(por hash), qual imagem, quais hiperparâmetros, qual job, qual artefato (por
tamanho e ETag), qual endpoint, qual resultado de smoke, quais métricas de teste
- e quais versões de ferramenta produziram tudo isso.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab1 import aws_helpers as aws
from lab1.config import (
    DATA_DIR,
    MANIFEST_FILE,
    MODEL_TRAIN_FILE,
    MODEL_VALIDATION_FILE,
    emit,
    evidence_dir,
    load_config,
    log,
)


def run_capture(command: list[str], cwd: Path | None = None) -> str | None:
    try:
        completed = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, check=True, timeout=60
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip()


def git_state(repo: Path) -> dict[str, str | bool | None]:
    sha = run_capture(["git", "rev-parse", "HEAD"], cwd=repo)
    status = run_capture(["git", "status", "--porcelain"], cwd=repo)
    return {
        "commit_sha": sha,
        "branch": run_capture(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo),
        "working_tree_clean": (status == "") if status is not None else None,
    }


def tool_versions(terraform_dir: Path) -> dict[str, object]:
    terraform_version: object = None
    raw = run_capture(["terraform", "version", "-json"], cwd=terraform_dir)
    if raw:
        parsed = json.loads(raw)
        terraform_version = {
            "terraform": parsed.get("terraform_version"),
            "providers": parsed.get("provider_selections", {}),
        }
    versions: dict[str, object] = {
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "terraform": terraform_version,
    }
    for module in ("boto3", "botocore", "numpy", "sklearn"):
        try:
            versions[module] = __import__(module).__version__
        except Exception:  # ferramenta opcional ausente não pode quebrar o relatório
            versions[module] = None
    return versions


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def channel_evidence(session, outputs: dict, data_dir: Path) -> dict[str, object]:
    """Faz HeadObject em cada canal de treino e compara o tamanho com o arquivo local.

    "O apply passou" não diz nada sobre o que o SageMaker vai ler. Esta é a
    fronteira de storage provada no nível do objeto: os bytes estão lá, e são a
    mesma quantidade de bytes que o gerador escreveu.
    """
    local = {"train": MODEL_TRAIN_FILE, "validation": MODEL_VALIDATION_FILE}
    channels = outputs.get("training_channels")
    proven: dict[str, object] = {}
    if not isinstance(channels, dict):
        return proven

    for name, uri in channels.items():
        object_uri = f"{str(uri).rstrip('/')}/{name}.csv"
        bucket, key = aws.split_s3_uri(object_uri)
        head = aws.object_exists(session, bucket, key)
        local_name = local.get(name)
        local_path = data_dir / local_name if local_name else None
        local_bytes = local_path.stat().st_size if local_path and local_path.exists() else None
        proven[name] = {
            "uri": object_uri,
            "exists": bool(head),
            "remote_bytes": (head or {}).get("content_length"),
            "etag": (head or {}).get("etag"),
            "local_bytes": local_bytes,
            "size_matches_local": bool(head)
            and local_bytes is not None
            and head.get("content_length") == local_bytes,
        }
    return proven


def to_markdown(evidence: dict) -> str:
    dataset = evidence["dataset"]
    training = evidence.get("training") or {}
    smoke = evidence.get("smoke") or {}
    evaluation = evidence.get("evaluation") or {}
    metrics = evaluation.get("metrics") or {}
    endpoint = evidence.get("endpoint") or {}

    def row(label: str, value: object) -> str:
        return f"| {label} | {'-' if value in (None, '', {}) else value} |"

    lines = [
        "# Lab 02 - pacote de evidências",
        "",
        f"Gerado em {evidence['generated_at_utc']} UTC.",
        "",
        "Um modelo não é um sistema de ML. Abaixo está a cadeia que transforma um",
        "no outro, cada elo registrado com algo conferível.",
        "",
        "## 1. Ambiente",
        "",
        "| Item | Valor |",
        "|---|---|",
        row("Conta AWS", evidence["aws"]["account_id"]),
        row("Região", evidence["aws"]["region"]),
        row("Chamador", evidence["aws"]["caller_arn"]),
        row("Role de execução", evidence["aws"]["execution_role_arn"]),
        row("Commit do Git", evidence["git"]["commit_sha"]),
        row("Árvore de trabalho limpa", evidence["git"]["working_tree_clean"]),
        row("Terraform", (evidence["versions"].get("terraform") or {}).get("terraform")),
        row("Provider AWS", (evidence["versions"].get("terraform") or {}).get("providers")),
        row("Python", evidence["versions"]["python"]),
        row("boto3 / botocore", f"{evidence['versions']['boto3']} / {evidence['versions']['botocore']}"),
        row("numpy / scikit-learn", f"{evidence['versions']['numpy']} / {evidence['versions']['sklearn']}"),
        "",
        "## 2. Dados (capacidade de storage)",
        "",
        "| Item | Valor |",
        "|---|---|",
        row("Bucket", evidence["aws"]["bucket_name"]),
        row("Semente", dataset.get("seed")),
        row("Versão do schema", dataset.get("schema_version")),
        row("Linhas", dataset.get("rows")),
        row("Prevalência no source", (dataset.get("source") or {}).get("prevalence")),
        "",
        "| Arquivo | Linhas | SHA-256 |",
        "|---|---|---|",
        f"| {(dataset.get('source') or {}).get('file')} | "
        f"{(dataset.get('source') or {}).get('rows')} | "
        f"`{(dataset.get('source') or {}).get('sha256')}` |",
    ]
    for name, split in (dataset.get("splits") or {}).items():
        lines.append(f"| {split.get('file')} ({name}) | {split.get('rows')} | `{split.get('sha256')}` |")

    channels_proven = evidence["aws"].get("input_channels") or {}
    if channels_proven:
        lines += [
            "",
            "Canais de treino como existem no S3 (HeadObject, não inferência):",
            "",
            "| Canal | Objeto | Bytes no S3 | Bate com o arquivo local |",
            "|---|---|---|---|",
        ]
        for name, channel in sorted(channels_proven.items()):
            lines.append(
                f"| {name} | `{channel.get('uri')}` | {channel.get('remote_bytes')} | "
                f"{channel.get('size_matches_local')} |"
            )

    lines += [
        "",
        "## 3. Treino (capacidade de treino)",
        "",
        "| Item | Valor |",
        "|---|---|",
        row("Training job", training.get("training_job_name")),
        row("Status", training.get("status")),
        row("Início", training.get("training_start_time")),
        row("Fim", training.get("training_end_time")),
        row("Segundos cobrados", training.get("billable_seconds")),
        row("Imagem", training.get("training_image")),
        row("Modo de entrada", training.get("training_input_mode")),
        row("Instância", f"{training.get('instance_count')} x {training.get('instance_type')}"),
        row("Volume (GB)", training.get("volume_size_in_gb")),
        row("Tempo máximo (s)", training.get("max_runtime_in_seconds")),
        row("Caminho de saída", training.get("output_s3_path")),
        "",
        "Hiperparâmetros como o SageMaker os aceitou:",
        "",
        "| Nome | Valor |",
        "|---|---|",
    ]
    for key, value in sorted((training.get("hyperparameters") or {}).items()):
        lines.append(f"| {key} | {value} |")

    lines += ["", "Canais de entrada:", "", "| Canal | URI no S3 | Content type |", "|---|---|---|"]
    for channel in training.get("input_channels") or []:
        lines.append(f"| {channel['channel']} | `{channel['s3_uri']}` | {channel['content_type']} |")

    if training.get("final_metrics"):
        lines += ["", "Métricas reportadas pelo container de treino:", "", "| Métrica | Valor |", "|---|---|"]
        for metric in training["final_metrics"]:
            lines.append(f"| {metric['name']} | {metric['value']} |")

    lines += [
        "",
        "## 4. Artefato do modelo",
        "",
        "| Item | Valor |",
        "|---|---|",
        row("URI do artefato", f"`{training.get('model_artifact_uri')}`" if training.get("model_artifact_uri") else None),
        row("Tamanho (bytes)", training.get("model_artifact_bytes")),
        row("ETag", training.get("model_artifact_etag")),
        row("Existência provada por", "s3:HeadObject antes de o Model ser criado"),
        "",
        "## 5. Serving (capacidade de predição)",
        "",
        "| Item | Valor |",
        "|---|---|",
        row("Model do SageMaker", evidence["terraform_outputs"].get("model_name")),
        row("Endpoint config", evidence["terraform_outputs"].get("endpoint_config_name")),
        row("Endpoint", evidence["terraform_outputs"].get("endpoint_name")),
        row("Status do endpoint", endpoint.get("status")),
        row("Instância", f"{endpoint.get('instance_count')} x {endpoint.get('instance_type')}"),
        "",
        "Requisição de smoke determinística:",
        "",
        "| Registro | Payload CSV | p(churn) |",
        "|---|---|---|",
    ]
    probabilities = smoke.get("probabilities") or {}
    for request in smoke.get("requests") or []:
        lines.append(f"| {request['name']} | `{request['csv']}` | {probabilities.get(request['name'])} |")
    if smoke:
        lines += [
            "",
            f"Verificações de smoke: **{'PASS' if smoke.get('passed') else 'FAIL'}** "
            f"({sum(1 for v in (smoke.get('checks') or {}).values() if v)}"
            f"/{len(smoke.get('checks') or {})}).",
        ]

    lines += [
        "",
        "## 6. Avaliação (capacidade de evidência)",
        "",
        "| Item | Valor |",
        "|---|---|",
        row("Amostras", metrics.get("samples")),
        row("Prevalência", metrics.get("prevalence")),
        row("Limiar de decisão", metrics.get("decision_threshold")),
        row("Acurácia da baseline majoritária", metrics.get("majority_baseline_accuracy")),
        row("Acurácia", metrics.get("accuracy")),
        row("Precisão", metrics.get("precision")),
        row("Recall", metrics.get("recall")),
        row("F1", metrics.get("f1")),
        row("ROC-AUC", metrics.get("roc_auc")),
        row("PR-AUC", metrics.get("pr_auc")),
        row("Supera a baseline", metrics.get("beats_majority_baseline")),
        "",
    ]
    if metrics.get("confusion_matrix"):
        cm = metrics["confusion_matrix"]
        lines += [
            "| | Previsto 0 | Previsto 1 |",
            "|---|---|---|",
            f"| **Real 0** | {cm['true_negative']} | {cm['false_positive']} |",
            f"| **Real 1** | {cm['false_negative']} | {cm['true_positive']} |",
            "",
        ]

    lines += [
        "## 7. Veredito",
        "",
        "| Elo | Resultado |",
        "|---|---|",
    ]
    for stage, passed in evidence["chain"].items():
        lines.append(f"| {stage} | {'PASS' if passed else 'AUSENTE/FAIL'} |")
    lines += ["", f"**Cadeia completa: {'sim' if evidence['passed'] else 'não'}**", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    cfg = load_config()
    out = evidence_dir()
    out.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[1]

    outputs: dict[str, object] = {}
    aws_block: dict[str, object] = {"region": cfg.region}
    endpoint_block: dict[str, object] = {}

    try:
        session = aws.make_session(cfg.region, args.profile)
        identity = aws.whoami(session)
        aws_block.update(
            {"account_id": identity["account_id"], "caller_arn": identity["arn"]}
        )
        outputs = aws.terraform_outputs()
        aws_block["bucket_name"] = outputs.get("bucket_name")
        aws_block["execution_role_arn"] = outputs.get("execution_role_arn")
        aws_block["input_channels"] = channel_evidence(session, outputs, args.data)

        endpoint_name = outputs.get("endpoint_name")
        if endpoint_name:
            description = aws.describe_endpoint(session, str(endpoint_name))
            variant = (description.get("ProductionVariants") or [{}])[0]
            endpoint_block = {
                "status": description.get("EndpointStatus"),
                "arn": description.get("EndpointArn"),
                "created_at": aws.json_safe(description.get("CreationTime")),
                "variant_name": variant.get("VariantName"),
                "instance_type": outputs.get("instance_type"),
                "instance_count": variant.get("CurrentInstanceCount"),
            }
    except aws.AwsError as exc:
        # A evidência ainda vale a pena sem acesso vivo à AWS - ela só registra
        # que os elos de serving estão faltando.
        log(f"[aviso] contexto de AWS/Terraform indisponível: {exc}")

    dataset = load_json(args.data / MANIFEST_FILE) or {}
    training = load_json(out / "training_job.json")
    smoke = load_json(out / "smoke_prediction.json")
    evaluation = load_json(out / "evaluation.json")

    channels = aws_block.get("input_channels") or {}
    chain = {
        "storage: dataset gerado e com hash registrado": bool(dataset),
        "storage: canais de treino comprovados no S3": bool(channels)
        and all(bool(c.get("size_matches_local")) for c in channels.values()),
        "training: job chegou a Completed": (training or {}).get("status") == "Completed",
        "artifact: model.tar.gz comprovado no S3": bool((training or {}).get("model_artifact_bytes")),
        "serving: endpoint InService": endpoint_block.get("status") == "InService",
        "serving: inferência de smoke determinística aprovada": bool((smoke or {}).get("passed")),
        "evidence: métricas de teste atendem a aceitação": bool((evaluation or {}).get("passed")),
    }

    evidence = {
        "generated_at_utc": aws.utc_now_iso(),
        "lab": "lab1-model-to-ml-system",
        "schema_version": cfg.schema_version,
        "aws": aws_block,
        "git": git_state(repo_root),
        "versions": tool_versions(repo_root / "terraform"),
        "terraform_outputs": outputs,
        "dataset": dataset,
        "training": training,
        "endpoint": endpoint_block,
        "smoke": smoke,
        "evaluation": evaluation,
        "chain": chain,
        "passed": all(chain.values()),
    }

    (out / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    (out / "evidence.md").write_text(to_markdown(evidence), encoding="utf-8")

    for stage, passed in chain.items():
        log(f"  [{'PASS' if passed else 'FAIL'}] {stage}")
    log(f"[{'PASS' if evidence['passed'] else 'FAIL'}] cadeia de evidências -> {out / 'evidence.md'}")

    emit({"evidence_json": str(out / "evidence.json"), "chain": chain, "passed": evidence["passed"]})
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
