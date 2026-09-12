#!/usr/bin/env python3
"""Ponte entre o estágio de treino e o estágio de serving.

O `aws_sagemaker_training_job` do provider 6.60.0 retorna assim que o job entra em
InProgress e não exporta a URI do artefato, então um `terraform apply` verde não
diz nada sobre a existência de um modelo. Este script fecha essa lacuna:

1. espera um status terminal de treino e mostra o `FailureReason` literalmente;
2. lê a URI do artefato no `DescribeTrainingJob` - o valor autoritativo, nunca um
   caminho montado a partir de convenção de nome;
3. prova com `HeadObject` que o objeto existe e não está vazio;
4. escreve `terraform/artifact.auto.tfvars.json` para o estágio dois do
   `make apply` não precisar de copiar e colar.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lab1 import aws_helpers as aws
from lab1.config import TERRAFORM_DIR, emit, evidence_dir, load_config, log

HANDOFF_FILE = "artifact.auto.tfvars.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--job-name", help="por padrão, o output training_job_name do Terraform")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--poll", type=int, default=20)
    args = parser.parse_args()

    cfg = load_config()
    try:
        session = aws.make_session(cfg.region, args.profile)
        job_name = args.job_name or aws.require_output(aws.terraform_outputs(), "training_job_name")

        log(f"[wait] esperando o training job {job_name}")
        description = aws.wait_training_job(
            session, job_name, poll_seconds=args.poll, timeout_seconds=args.timeout
        )
        status = description["TrainingJobStatus"]
        if status != "Completed":
            reason = description.get("FailureReason", "(nenhum FailureReason reportado)")
            log(f"[FAIL] o training job {job_name} terminou como {status}: {reason}")
            log("[dica] logs completos: grupo /aws/sagemaker/TrainingJobs no CloudWatch")
            emit({"passed": False, "training_job_name": job_name, "status": status, "failure_reason": reason})
            return 1

        artifact_uri = description.get("ModelArtifacts", {}).get("S3ModelArtifacts", "")
        if not artifact_uri:
            raise aws.AwsError(
                f"o training job {job_name} terminou, mas não reportou ModelArtifacts.S3ModelArtifacts"
            )

        bucket, key = aws.split_s3_uri(artifact_uri)
        head = aws.object_exists(session, bucket, key)
        if head is None:
            raise aws.AwsError(f"o artefato {artifact_uri} não está no S3 - recusando publicar um modelo")
        if head["content_length"] <= 0:
            raise aws.AwsError(f"o artefato {artifact_uri} está vazio - recusando publicar um modelo")
    except aws.AwsError as exc:
        log(f"[FAIL] {exc}")
        emit({"passed": False, "error": str(exc)})
        return 1

    handoff = TERRAFORM_DIR / HANDOFF_FILE
    # O `deploy_serving` é persistido aqui, não passado como `-var`, para um
    # `terraform plan` ou `destroy` posterior ver o mesmo estágio que o state
    # representa.
    handoff.write_text(
        json.dumps({"deploy_serving": True, "model_artifact_uri": artifact_uri}, indent=2) + "\n",
        encoding="utf-8",
    )

    # Guardado para o pacote de evidências: estes campos são a prova de que o
    # treino rodou de fato com a configuração que o repositório afirma.
    record = {
        "passed": True,
        "training_job_name": job_name,
        "status": status,
        "secondary_status": description.get("SecondaryStatus"),
        "creation_time": aws.json_safe(description.get("CreationTime")),
        "training_start_time": aws.json_safe(description.get("TrainingStartTime")),
        "training_end_time": aws.json_safe(description.get("TrainingEndTime")),
        "billable_seconds": description.get("BillableTimeInSeconds"),
        "training_image": description.get("AlgorithmSpecification", {}).get("TrainingImage"),
        "training_input_mode": description.get("AlgorithmSpecification", {}).get("TrainingInputMode"),
        "hyperparameters": description.get("HyperParameters", {}),
        "input_channels": [
            {
                "channel": channel.get("ChannelName"),
                "s3_uri": channel.get("DataSource", {}).get("S3DataSource", {}).get("S3Uri"),
                "content_type": channel.get("ContentType"),
            }
            for channel in description.get("InputDataConfig", [])
        ],
        "output_s3_path": description.get("OutputDataConfig", {}).get("S3OutputPath"),
        "instance_type": description.get("ResourceConfig", {}).get("InstanceType"),
        "instance_count": description.get("ResourceConfig", {}).get("InstanceCount"),
        "volume_size_in_gb": description.get("ResourceConfig", {}).get("VolumeSizeInGB"),
        "max_runtime_in_seconds": description.get("StoppingCondition", {}).get("MaxRuntimeInSeconds"),
        "final_metrics": [
            {"name": m.get("MetricName"), "value": m.get("Value")}
            for m in description.get("FinalMetricDataList", [])
        ],
        "model_artifact_uri": artifact_uri,
        "model_artifact_bytes": head["content_length"],
        "model_artifact_etag": head["etag"],
        "handoff_file": str(handoff),
    }

    out = evidence_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "training_job.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    log(f"[wait] {status} em {record['billable_seconds']}s cobrados")
    for metric in record["final_metrics"]:
        log(f"[wait] métrica final {metric['name']}={metric['value']}")
    log(f"[wait] artefato {artifact_uri} ({head['content_length']} bytes, conferido com HeadObject)")
    log(f"[wait] escrevi {handoff.name} para o estágio de serving")

    emit(record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
