#!/usr/bin/env python3
"""Ponto único de entrada do Lab 04.1.

Um comando por etapa do ciclo de vida, na ordem em que o README os apresenta:

    doctor -> data -> validate-data -> wait-training -> status -> dashboard
           -> baseline -> drift -> alarm-status -> reaction -> ground-truth
           -> evidence -> resumo -> verify-clean

Disciplina de saída, uniforme em todos eles: **stdout é resultado** (JSON que
alguém vai capturar ou pipar), **stderr é progresso**. Quem rodar
`make status > status.json` recebe um arquivo válido.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fiap_ml_operations import (  # noqa: E402
    aws,
    config,
    data,
    drift,
    evaluation,
    evidence,
    monitoring,
)
from fiap_ml_operations.aws import LabError, emit, log  # noqa: E402

PASS, FAIL = "[PASS]", "[FAIL]"


class Checks:
    """Coletor de verificações no formato que o aluno lê no terminal.

    O nome do check NÃO é traduzido: ele é um endereço. O aluno cola
    `terraform_version` no README (ou no Google) para achar o que reprovou. O
    detalhe ao lado é frase, e por isso é em português.
    """

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.items.append({"check": name, "passed": passed, "detail": detail})
        marker = PASS if passed else FAIL
        log(f"{marker} {name}: {detail}")

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [item for item in self.items if not item["passed"]]

    def summary(self, title: str) -> dict[str, Any]:
        ok = not self.failed
        log("")
        log(
            f"{PASS if ok else FAIL} {title}: "
            f"{len(self.items) - len(self.failed)}/{len(self.items)} verificações passaram"
        )
        return {
            "passed": ok,
            "checks_total": len(self.items),
            "checks_failed": len(self.failed),
            "checks": self.items,
        }


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def _required_terraform_version() -> str | None:
    """Lê o required_version do próprio versions.tf.

    Existe porque imprimir a versão instalada não é conferir a versão instalada.
    O `required_version` só é avaliado no `terraform init`, que neste lab mora
    dentro do `make validate` — ou seja, uma divergência de versão só apareceria
    no passo do `plan`, depois de o aluno já ter visto quatro comandos verdes.
    Comparar aqui move essa falha para o primeiro minuto do lab.
    """
    versions_tf = config.TERRAFORM_DIR / "versions.tf"
    if not versions_tf.exists():
        return None
    match = re.search(
        r'required_version\s*=\s*"=\s*([0-9]+\.[0-9]+\.[0-9]+)"',
        versions_tf.read_text(encoding="utf-8"),
    )
    return match.group(1) if match else None


def _installed_terraform_version() -> str | None:
    binary = shutil.which(aws._terraform_binary()) or aws._terraform_binary()
    try:
        result = subprocess.run(  # noqa: S603 - binário resolvido, sem shell
            [binary, "version", "-json"], capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)["terraform_version"]
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        return None


def cmd_doctor(_: argparse.Namespace) -> int:
    checks = Checks()
    cfg = config.load_config()
    region_esperada = cfg["region"]

    major, minor = sys.version_info[:2]
    checks.add(
        "python_version",
        (major, minor) >= (3, 11),
        f"Python {major}.{minor} (o lab pede 3.11 ou mais novo)",
    )

    instalada = _installed_terraform_version()
    exigida = _required_terraform_version()
    if instalada is None:
        checks.add("terraform_version", False, "terraform não encontrado no PATH")
    elif exigida is None:
        checks.add(
            "terraform_version", False, "não foi possível ler required_version de versions.tf"
        )
    else:
        checks.add(
            "terraform_version",
            instalada == exigida,
            f"instalado {instalada}, versions.tf exige exatamente {exigida}"
            + ("" if instalada == exigida else " — rode o setup da disciplina para alinhar"),
        )

    try:
        identity = aws.caller_identity()
        checks.add(
            "aws_credentials",
            True,
            f"conta {identity['Account']} (nenhuma credencial é impressa)",
        )
    except LabError as exc:
        checks.add("aws_credentials", False, str(exc))
        emit(checks.summary("doctor"))
        return 1

    sessao = aws.client("sts").meta.region_name
    checks.add(
        "aws_region",
        sessao == region_esperada,
        f"sessão em {sessao}, o lab exige {region_esperada}",
    )

    try:
        arn = aws.role_arn(cfg["aws"]["execution_role_name"])
        checks.add("lab_role", True, arn)
    except LabError as exc:
        checks.add("lab_role", False, str(exc))

    # Alcançabilidade de serviço: uma chamada de leitura barata por serviço que o lab
    # usa. Falha aqui é permissão ou região, e é melhor descobrir agora que no apply.
    sondas: list[tuple[str, Callable[[], Any], str]] = [
        (
            "sagemaker_reachable",
            lambda: aws.client("sagemaker").list_endpoints(MaxResults=1),
            "ListEndpoints respondeu",
        ),
        (
            "cloudwatch_reachable",
            lambda: aws.client("cloudwatch").list_metrics(Namespace=cfg["monitoring"]["namespace"]),
            "ListMetrics respondeu",
        ),
        (
            "eventbridge_reachable",
            lambda: aws.client("events").list_rules(Limit=1),
            "ListRules respondeu",
        ),
        (
            "lambda_reachable",
            lambda: aws.client("lambda").list_functions(MaxItems=1),
            "ListFunctions respondeu",
        ),
        (
            "s3_reachable",
            lambda: aws.client("s3").list_buckets(),
            "ListBuckets respondeu",
        ),
        (
            "logs_reachable",
            lambda: aws.client("logs").describe_log_groups(limit=1),
            "DescribeLogGroups respondeu",
        ),
    ]
    for nome, sonda, ok_detalhe in sondas:
        try:
            sonda()
            checks.add(nome, True, ok_detalhe)
        except Exception as exc:  # noqa: BLE001 - qualquer falha aqui é diagnóstico
            checks.add(nome, False, f"{type(exc).__name__}: {exc}")

    resultado = checks.summary("doctor")
    emit(resultado)
    return 0 if resultado["passed"] else 1


# --------------------------------------------------------------------------- #
# data / validate-data
# --------------------------------------------------------------------------- #


def cmd_data(_: argparse.Namespace) -> int:
    log("[data] gerando o dataset determinístico")
    manifest = data.write_all()
    for nome, info in manifest["windows"].items():
        log(f"[data] {nome}: {info['rows']} linhas, prevalência {info['prevalence']:.4f}")
    log(f"[data] escrito em {config.DATA_DIR}")
    emit(manifest)
    return 0


def cmd_validate_data(_: argparse.Namespace) -> int:
    """Contrato de dados executável — o go/no-go antes de gastar nuvem."""
    cfg = config.load_config()
    checks = Checks()
    out = config.DATA_DIR

    manifest_path = out / "dataset_manifest.json"
    if not manifest_path.exists():
        raise LabError("dataset não encontrado. Rode `make data` antes.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    features = config.feature_order()
    checks.add(
        "schema.feature_order",
        manifest["feature_order"] == features,
        f"{len(features)} features na ordem herdada do Lab 02",
    )
    checks.add(
        "schema.label_and_id",
        manifest["label"] == config.label_column()
        and manifest["id_column"] == config.id_column(),
        f"rótulo `{manifest['label']}`, identificador `{manifest['id_column']}`",
    )

    linhagem = manifest.get("lineage", {})
    checks.add(
        "manifest.lineage",
        linhagem.get("source_lab") == cfg["lineage"]["source_lab"]
        and linhagem.get("business_capability") == cfg["lineage"]["business_capability"],
        f"{linhagem.get('business_capability')} vinda de {linhagem.get('source_lab')}",
    )

    esperado = {
        "train": int(round(cfg["dataset"]["rows"] * cfg["dataset"]["split"]["train"])),
        "validation": int(
            round(cfg["dataset"]["rows"] * cfg["dataset"]["split"]["validation"])
        ),
        "reference": cfg["dataset"]["rows"]
        - int(round(cfg["dataset"]["rows"] * cfg["dataset"]["split"]["train"]))
        - int(round(cfg["dataset"]["rows"] * cfg["dataset"]["split"]["validation"])),
        "production_baseline": int(cfg["dataset"]["production_windows"]["baseline_rows"]),
        "production_shifted": int(cfg["dataset"]["production_windows"]["shifted_rows"]),
    }
    for janela, quantidade in esperado.items():
        real = manifest["windows"][janela]["rows"]
        checks.add(
            f"rows.{janela}", real == quantidade, f"{real} linhas (esperado {quantidade})"
        )

    minimo = int(cfg["min_rows_per_split"])
    checks.add(
        "rows.min_per_split",
        all(manifest["windows"][j]["rows"] >= minimo for j in ("train", "validation", "reference")),
        f"treino/validação/referência têm pelo menos {minimo} linhas",
    )

    # ---- valores: NaN/Inf, limites, unicidade de id, rótulo binário ---------
    todos_ids: list[str] = []
    problemas_nan: list[str] = []
    problemas_bounds: list[str] = []
    bounds = cfg["bounds"]

    janelas_com_id = (
        "reference.csv",
        "production_baseline.csv",
        "production_shifted.csv",
    )
    for arquivo in janelas_com_id:
        ids, valores = data.read_window(out / arquivo)
        todos_ids.extend(ids)
        for nome, coluna in valores.items():
            if not np.all(np.isfinite(coluna)):
                problemas_nan.append(f"{arquivo}:{nome}")
            lo, hi = bounds[nome]
            if coluna.min() < lo or coluna.max() > hi:
                problemas_bounds.append(
                    f"{arquivo}:{nome} fora de [{lo}, {hi}] "
                    f"(min {coluna.min():g}, max {coluna.max():g})"
                )

    # Os canais de treino não têm cabeçalho nem id: são lidos como matriz pura.
    for arquivo in ("train.csv", "validation.csv"):
        matriz = np.loadtxt(out / arquivo, delimiter=",")
        if not np.all(np.isfinite(matriz)):
            problemas_nan.append(arquivo)
        rotulos = matriz[:, 0]
        if not np.all(np.isin(rotulos, (0.0, 1.0))):
            problemas_bounds.append(f"{arquivo}: rótulo fora de {{0,1}}")
        for indice, nome in enumerate(features, start=1):
            lo, hi = bounds[nome]
            coluna = matriz[:, indice]
            if coluna.min() < lo or coluna.max() > hi:
                problemas_bounds.append(f"{arquivo}:{nome} fora de [{lo}, {hi}]")

    checks.add(
        "values.no_nan_or_inf",
        not problemas_nan,
        "nenhum NaN ou infinito" if not problemas_nan else ", ".join(problemas_nan),
    )
    checks.add(
        "values.bounds",
        not problemas_bounds,
        "todas as colunas dentro dos limites do contrato"
        if not problemas_bounds
        else "; ".join(problemas_bounds[:3]),
    )
    checks.add(
        "ids.unique",
        len(todos_ids) == len(set(todos_ids)),
        f"{len(todos_ids)} identificadores, todos distintos entre as janelas",
    )

    prevalencias = [manifest["windows"][j]["prevalence"] for j in ("train", "validation", "reference")]
    lo_prev, hi_prev = cfg["prevalence_range"]
    checks.add(
        "target.prevalence",
        all(lo_prev <= p <= hi_prev for p in prevalencias),
        f"prevalência entre {min(prevalencias):.4f} e {max(prevalencias):.4f} "
        f"(faixa aceita [{lo_prev}, {hi_prev}])",
    )

    # ---- payload de inferência não pode conter rótulo nem identificador ----
    header_producao, _ = data.read_csv(out / "production_shifted.csv")
    checks.add(
        "payload.no_target",
        config.label_column() not in header_producao,
        f"a janela de produção não traz a coluna `{config.label_column()}`: "
        "o rótulo ainda não existe no mundo quando a predição acontece",
    )
    checks.add(
        "windows.schema_preserved",
        all(
            data.read_csv(out / arquivo)[0] == [config.id_column(), *features]
            for arquivo in ("production_baseline.csv", "production_shifted.csv")
        ),
        "as duas janelas de produção preservam identificador + ordem de features",
    )

    # ---- ground truth casa 1:1 com as janelas ------------------------------
    for janela, arquivo_gt in (
        ("production_baseline", "ground_truth_baseline.csv"),
        ("production_shifted", "ground_truth_shifted.csv"),
    ):
        ids, _ = data.read_window(out / f"{janela}.csv")
        rotulos = data.read_labels(out / arquivo_gt)
        checks.add(
            f"ground_truth.{janela}",
            set(ids) == set(rotulos) and set(rotulos.values()) <= {0, 1},
            f"{len(rotulos)} rótulos binários, um para cada linha da janela",
        )

    # ---- hashes determinísticos -------------------------------------------
    log("")
    log("[validate-data] hashes SHA-256 dos arquivos base:")
    divergentes = []
    for nome, info in manifest["files"].items():
        atual = data.sha256(out / nome)
        igual = atual == info["sha256"]
        if not igual:
            divergentes.append(nome)
        log(f"  {nome:30s} {atual}")
    checks.add(
        "hashes.deterministic",
        not divergentes,
        "todos os arquivos conferem com o manifesto"
        if not divergentes
        else f"divergentes: {', '.join(divergentes)}",
    )

    resultado = checks.summary("contrato de dados")
    resultado["hashes"] = {n: data.sha256(out / n) for n in manifest["files"]}
    emit(resultado)
    return 0 if resultado["passed"] else 1


# --------------------------------------------------------------------------- #
# wait-training
# --------------------------------------------------------------------------- #


def cmd_wait_training(_: argparse.Namespace) -> int:
    job = aws.output("training_job_name")
    log(f"[wait] esperando o training job {job}")
    described = aws.wait_training_job(job)

    artefato = described["ModelArtifacts"]["S3ModelArtifacts"]
    log(f"[wait] artefato informado pela API: {artefato}")

    head = aws.head_object(artefato)
    log(f"[wait] HeadObject confirmou {head['ContentLength']} bytes")

    destino = aws.write_handoff(artefato)
    log(f"[wait] URI gravada em {destino.name} para o estágio 2 do apply")

    faturado = described.get("BillableTimeInSeconds")
    emit(
        {
            "training_job_name": job,
            "status": described["TrainingJobStatus"],
            "model_artifact_uri": artefato,
            "artifact_bytes": head["ContentLength"],
            "billable_seconds": faturado,
        }
    )
    return 0


# --------------------------------------------------------------------------- #
# status / dashboard
# --------------------------------------------------------------------------- #


def cmd_status(_: argparse.Namespace) -> int:
    outputs = aws.terraform_outputs()
    endpoint = outputs.get("endpoint_name") or ""
    if not endpoint:
        raise LabError("o estágio de serving não foi aplicado. Rode `make apply`.")

    sagemaker = aws.client("sagemaker")
    descricao = sagemaker.describe_endpoint(EndpointName=endpoint)
    variantes = [
        {
            "nome": v["VariantName"],
            "instancias_atuais": v.get("CurrentInstanceCount"),
            "instancias_desejadas": v.get("DesiredInstanceCount"),
            "tipo": v.get("CurrentServerlessConfig", {}).get("MemorySizeInMB")
            or outputs.get("instance_type"),
        }
        for v in descricao.get("ProductionVariants", [])
    ]

    alarme = monitoring.alarm_state(outputs["alarm_name"])
    painel = monitoring.dashboard_summary(outputs["dashboard_name"])
    funcao = aws.client("lambda").get_function_configuration(
        FunctionName=outputs["lambda_function_name"]
    )
    regra = aws.client("events").describe_rule(Name=outputs["event_rule_name"])
    alvos = aws.client("events").list_targets_by_rule(Rule=outputs["event_rule_name"])

    emit(
        {
            "endpoint": {
                "nome": endpoint,
                "status": descricao["EndpointStatus"],
                "criado_em": descricao.get("CreationTime"),
                "config": descricao.get("EndpointConfigName"),
                "variantes": variantes,
            },
            "model_lineage": outputs.get("model_lineage"),
            "alarme": alarme,
            "dashboard": painel,
            "lambda": {
                "nome": funcao["FunctionName"],
                "runtime": funcao["Runtime"],
                "timeout_s": funcao["Timeout"],
                "ultima_modificacao": funcao["LastModified"],
            },
            "eventbridge": {
                "regra": regra["Name"],
                "estado": regra["State"],
                "alvos": [alvo["Arn"] for alvo in alvos.get("Targets", [])],
            },
        }
    )
    return 0


def cmd_dashboard(_: argparse.Namespace) -> int:
    outputs = aws.terraform_outputs()
    nome = outputs.get("dashboard_name") or ""
    url = outputs.get("dashboard_url") or ""
    if not nome:
        raise LabError("o dashboard só existe depois do `make apply`.")

    resumo = monitoring.dashboard_summary(nome)

    log("")
    log(f"  Dashboard : {nome}")
    log(f"  Widgets   : {resumo['widgets']}")
    log("")
    log("  Abra o link abaixo e DEIXE ABERTO durante o lab inteiro. Ele atualiza")
    log("  sozinho conforme novas métricas chegam (granularidade de 60 s).")
    log("")
    print(url)
    return 0


# --------------------------------------------------------------------------- #
# baseline / drift
# --------------------------------------------------------------------------- #


def _reference_scores(endpoint: str, features: list[str]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Scores do modelo na referência, calculados uma vez e reaproveitados.

    A referência é a régua do PSI de predições. Ela tem 600 linhas: recalcular a
    cada janela dobraria as invocações sem mudar o número, então o resultado fica em
    cache no disco. Rodar `make baseline` duas vezes reusa o cache.
    """
    cache = config.PREDICTIONS_DIR / "reference.json"
    _, valores = data.read_window(config.DATA_DIR / "reference.csv")

    if cache.exists():
        guardado = json.loads(cache.read_text(encoding="utf-8"))
        if guardado.get("endpoint") == endpoint:
            log(f"[predicao] reusando {len(guardado['scores'])} scores da referência em cache")
            return np.array(guardado["scores"], dtype=float), valores

    log("[predicao] pontuando as 600 linhas da referência (régua do PSI de predições)")
    linhas = [[float(valores[f][i]) for f in features] for i in range(len(valores[features[0]]))]
    scores = aws.invoke_endpoint_csv(endpoint, linhas)
    config.ensure_dirs()
    cache.write_text(
        json.dumps({"endpoint": endpoint, "scores": scores.tolist()}, indent=2),
        encoding="utf-8",
    )
    return scores, valores


def _observe_window(window: str, arquivo: str, evidencia: str) -> dict[str, Any]:
    """Núcleo comum de `baseline` e `drift`: invoca, mede, publica, registra."""
    cfg = config.load_config()
    features = config.feature_order()
    limiar = float(cfg["monitoring"]["alarm_threshold"])
    bins = int(cfg["drift"]["bins"])
    epsilon = float(cfg["drift"]["epsilon"])

    endpoint = aws.output("endpoint_name")
    aws.require_endpoint_in_service(endpoint)

    ref_scores, ref_features = _reference_scores(endpoint, features)

    ids, obs_features = data.read_window(config.DATA_DIR / arquivo)
    log(f"[predicao] invocando o endpoint para {len(ids)} clientes da janela {window}")
    linhas = [[float(obs_features[f][i]) for f in features] for i in range(len(ids))]
    obs_scores = aws.invoke_endpoint_csv(endpoint, linhas)

    # Predições guardadas para o ground truth atrasado poder juntá-las com os
    # rótulos que chegarem depois — sem reinvocar o endpoint, que a essa altura
    # pode já nem existir.
    config.ensure_dirs()
    (config.PREDICTIONS_DIR / f"{window}.json").write_text(
        json.dumps(
            {
                "window": window,
                "endpoint": endpoint,
                "gerado_em": datetime.now(timezone.utc).isoformat(),
                "predicoes": {obs_id: score for obs_id, score in zip(ids, obs_scores.tolist())},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    drifts = drift.feature_drift(ref_features, obs_features, features, bins=bins, epsilon=epsilon)
    psi_max = max(d.psi for d in drifts)
    responsaveis = drift.responsible_features(drifts, limiar)
    prediction_psi, _, _, _ = drift.psi(ref_scores, obs_scores, bins=bins, epsilon=epsilon)

    decisao = float(cfg["acceptance"]["decision_threshold"])
    taxa_prevista = float((obs_scores >= decisao).mean())
    prob_media = float(obs_scores.mean())

    log("")
    log(f"[drift] janela {window}: PSI máximo {psi_max:.4f} (limiar {limiar})")
    for d in sorted(drifts, key=lambda x: x.psi, reverse=True):
        marca = "  <-- acima do limiar" if d.psi >= limiar else ""
        log(f"  {d.feature:22s} PSI={d.psi:7.4f}  {d.interpretation} ({d.binning}){marca}")
    log("")
    log(f"[drift] PSI das predições: {prediction_psi:.4f}")
    log(f"[drift] churn previsto: {taxa_prevista:.4f} | probabilidade média: {prob_media:.4f}")
    log("")

    monitoring.publish(
        monitoring.namespace(),
        monitoring.window_metrics(
            endpoint_name=endpoint,
            window=window,
            psi_max=psi_max,
            feature_psi={d.feature: d.psi for d in drifts},
            prediction_psi=prediction_psi,
            predicted_churn_rate=taxa_prevista,
            mean_churn_probability=prob_media,
        ),
    )

    payload = {
        "window": window,
        "endpoint": endpoint,
        "medido_em": datetime.now(timezone.utc).isoformat(),
        "limiar": limiar,
        "bins": bins,
        "epsilon": epsilon,
        "psi_max": round(psi_max, 6),
        "feature_com_maior_psi": max(drifts, key=lambda d: d.psi).feature,
        "features_acima_do_limiar": [
            {"feature": d.feature, "psi": round(d.psi, 6), "leitura": d.interpretation}
            for d in responsaveis
        ],
        "psi_por_feature": [
            {
                "feature": d.feature,
                "psi": round(d.psi, 6),
                "leitura": d.interpretation,
                "discretizacao": d.binning,
                "bins": d.bins,
            }
            for d in drifts
        ],
        "prediction_psi": round(prediction_psi, 6),
        "predicted_churn_rate": round(taxa_prevista, 6),
        "mean_churn_probability": round(prob_media, 6),
        "linhas": len(ids),
    }
    evidence.write(evidencia, payload)
    return payload


def cmd_baseline(_: argparse.Namespace) -> int:
    payload = _observe_window("baseline", "production_baseline.csv", "baseline-drift.json")

    # O baseline não termina em "o PSI está baixo": termina provando que o alarme
    # está OK. Se ele já estivesse em ALARM aqui, a transição observada no `make
    # drift` não provaria nada.
    alarme = monitoring.alarm_state(aws.output("alarm_name"))
    log(f"[alarme] estado após o baseline: {alarme['estado']}")
    if alarme["estado"] == "ALARM":
        raise LabError(
            f"o alarme já está em ALARM depois do baseline (PSI {payload['psi_max']}). "
            "A janela saudável deveria manter o alarme em OK. Rode `make destroy` e "
            "recomece, ou confira se um `make drift` anterior publicou nesta janela."
        )

    payload["alarme_apos_baseline"] = alarme["estado"]
    evidence.write("baseline-drift.json", payload)
    emit(payload)
    return 0


def cmd_drift(_: argparse.Namespace) -> int:
    payload = _observe_window("drift", "production_shifted.csv", "production-drift.json")

    log("[drift] variáveis responsáveis, da maior para a menor:")
    for item in payload["features_acima_do_limiar"]:
        log(f"  {item['feature']:22s} PSI={item['psi']:.4f}  {item['leitura']}")
    log("")
    log("[drift] o PSI mede distribuição, não acerto. A queda de qualidade só será")
    log("[drift] conhecida no `make ground-truth`, quando o rótulo verdadeiro chegar.")

    emit(payload)
    return 0


# --------------------------------------------------------------------------- #
# alarm-status / reaction
# --------------------------------------------------------------------------- #


def cmd_alarm_status(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    nome = aws.output("alarm_name")

    if args.wait_for_alarm:
        estado = monitoring.wait_for_alarm_state(
            nome, "ALARM", int(cfg["monitoring"]["alarm_wait_timeout_s"])
        )
    else:
        estado = monitoring.alarm_state(nome)
        log(f"[alarme] {estado['estado']}: {estado['motivo']}")

    estado["historico"] = monitoring.alarm_history(nome)
    estado["url"] = aws.terraform_outputs().get("alarm_url")

    log("")
    log("[alarme] transições recentes:")
    for item in estado["historico"][:5]:
        log(f"  {item['data']}  {item['resumo']}")

    evidence.write("alarm.json", estado)
    emit(estado)
    return 0


def cmd_reaction(_: argparse.Namespace) -> int:
    """Espera o incidente aparecer e valida o que a Lambda escreveu."""
    cfg = config.load_config()
    outputs = aws.terraform_outputs()
    bucket = outputs["bucket_name"]
    prefixo = outputs["incidents_prefix"]
    funcao = outputs["lambda_function_name"]
    limite = int(cfg["monitoring"]["reaction_wait_timeout_s"])

    # Momento a partir do qual contamos: a transição do alarme. Contar invocação da
    # Lambda desde o começo do lab misturaria execuções de uma tentativa anterior.
    alarme = monitoring.alarm_state(outputs["alarm_name"])
    if alarme["estado"] != "ALARM":
        raise LabError(
            f"o alarme está em {alarme['estado']}, não em ALARM. Rode `make drift` e "
            "`make alarm-status` antes de esperar a reação."
        )

    log(f"[reacao] esperando o incidente em s3://{bucket}/{prefixo}/ (teto {limite}s)")
    prazo = time.monotonic() + limite
    objetos: list[dict[str, Any]] = []
    while True:
        objetos = aws.list_objects(bucket, f"{prefixo}/")
        if objetos:
            break
        if time.monotonic() >= prazo:
            raise LabError(
                f"nenhum incidente apareceu em s3://{bucket}/{prefixo}/ em {limite}s. "
                f"Confira o log da Lambda: aws logs tail /aws/lambda/{funcao} --since 15m"
            )
        log("[reacao] ainda nada; nova consulta em 10s")
        time.sleep(10)

    mais_recente = max(objetos, key=lambda o: o["LastModified"])
    incidente = aws.get_json_object(bucket, mais_recente["Key"])
    log(f"[reacao] incidente encontrado: s3://{bucket}/{mais_recente['Key']}")

    # Validação de conteúdo: o arquivo existir não prova que a decisão certa foi
    # registrada.
    problemas = []
    if incidente.get("status") != "INVESTIGATE":
        problemas.append(f"status inesperado: {incidente.get('status')!r}")
    if incidente.get("recommended_action") != "HOLD_PROMOTION_AND_VALIDATE_GROUND_TRUTH":
        problemas.append(f"ação inesperada: {incidente.get('recommended_action')!r}")
    if (incidente.get("alarme") or {}).get("nome") != outputs["alarm_name"]:
        problemas.append("o incidente não cita o alarme deste lab")
    if problemas:
        raise LabError("o incidente foi escrito, mas com conteúdo inesperado: " + "; ".join(problemas))

    # Quantas vezes a Lambda rodou, lido do log e não do "deve ter rodado".
    desde = int((datetime.now(timezone.utc) - timedelta(minutes=30)).timestamp() * 1000)
    eventos = aws.client("logs").filter_log_events(
        logGroupName=outputs["lambda_log_group"],
        startTime=desde,
        filterPattern="REPORT",
    )
    invocacoes = len(eventos.get("events", []))

    # A prova negativa que a aula precisa: a reação NÃO retreinou nada.
    #
    # O NameContains limita a busca ao prefixo DESTE lab. Sem ele, um training job de
    # outro laboratório do próprio aluno rodando na mesma conta apareceria na contagem
    # e a prova negativa daria um falso positivo.
    jobs = aws.client("sagemaker").list_training_jobs(
        NameContains=config.load_config()["aws"]["bucket_prefix"],
        CreationTimeAfter=alarme["atualizado_em"],
        MaxResults=10,
    )
    criados_depois = len(jobs.get("TrainingJobSummaries", []))

    payload = {
        "objeto_s3": f"s3://{bucket}/{mais_recente['Key']}",
        "tamanho_bytes": mais_recente["Size"],
        "incidente": incidente,
        "invocacoes_no_log": invocacoes,
        "log_group": outputs["lambda_log_group"],
        "training_jobs_criados_depois": criados_depois,
        "nao_automatizado": incidente.get("nao_automatizado", []),
    }

    log("")
    log(f"[reacao] decisão registrada : {incidente['status']}")
    log(f"[reacao] ação recomendada   : {incidente['recommended_action']}")
    log(f"[reacao] invocações no log  : {invocacoes}")
    log(f"[reacao] training jobs novos: {criados_depois} (esperado 0 — a reação não retreina)")

    evidence.write("reaction.json", payload)
    emit(payload)
    return 0


# --------------------------------------------------------------------------- #
# ground-truth
# --------------------------------------------------------------------------- #


def _carregar_predicoes(window: str) -> dict[str, float]:
    """Predições salvas por `make baseline` / `make drift`.

    A chave é o nome da JANELA na métrica (`baseline`/`drift`), não o nome do arquivo
    de dados (`production_baseline.csv`) — é assim que `_observe_window` as grava, e
    é o mesmo rótulo que aparece na dimensão `Window` do CloudWatch.
    """
    caminho = config.PREDICTIONS_DIR / f"{window}.json"
    if not caminho.exists():
        raise LabError(
            f"não há predições salvas para a janela {window}. Rode `make {window}` antes "
            "de `make ground-truth`."
        )
    return json.loads(caminho.read_text(encoding="utf-8"))["predicoes"]


def cmd_ground_truth(_: argparse.Namespace) -> int:
    cfg = config.load_config()
    limiar_decisao = float(cfg["acceptance"]["decision_threshold"])
    endpoint = aws.output("endpoint_name")

    log("[ground-truth] os rótulos verdadeiros chegaram: juntando com as predições salvas")

    relatorios: dict[str, evaluation.QualityReport] = {}
    for chave, janela, arquivo_gt in (
        ("baseline", "production_baseline", "ground_truth_baseline.csv"),
        ("drift", "production_shifted", "ground_truth_shifted.csv"),
    ):
        predicoes = _carregar_predicoes(chave)
        rotulos = data.read_labels(config.DATA_DIR / arquivo_gt)

        faltando = set(predicoes) - set(rotulos)
        if faltando:
            raise LabError(
                f"{len(faltando)} predições da janela {janela} não têm rótulo correspondente: "
                "a junção por identificador falhou."
            )

        ids = sorted(predicoes)
        scores = np.array([predicoes[i] for i in ids], dtype=float)
        verdades = np.array([rotulos[i] for i in ids], dtype=int)
        relatorios[chave] = evaluation.evaluate(chave, scores, verdades, limiar_decisao)

        r = relatorios[chave]
        log("")
        log(f"[ground-truth] janela {chave} ({r.rows} clientes)")
        log(f"  F1            : {r.f1:.4f}")
        log(f"  ROC-AUC       : {r.roc_auc:.4f}")
        log(f"  precisão      : {r.precision:.4f}")
        log(f"  recall        : {r.recall:.4f}")
        log(f"  churn previsto: {r.predicted_churn_rate:.4f}  |  real: {r.actual_churn_rate:.4f}")
        log(f"  matriz        : VN={r.true_negatives} FP={r.false_positives} "
            f"FN={r.false_negatives} VP={r.true_positives}")
        log(f"  modo de falha : {r.failure_mode}")

    comparacao = evaluation.compare(relatorios["baseline"], relatorios["drift"])

    log("")
    log(f"[ground-truth] queda de F1: {comparacao['queda_f1']:.4f}")
    log(f"[ground-truth] {comparacao['leitura']}")

    dados = []
    for chave, relatorio in relatorios.items():
        dados += monitoring.quality_metrics(endpoint, chave, relatorio.f1, relatorio.roc_auc)
    monitoring.publish(monitoring.namespace(), dados)

    payload = {
        "medido_em": datetime.now(timezone.utc).isoformat(),
        "threshold_de_decisao": limiar_decisao,
        "janelas": {chave: r.as_dict() for chave, r in relatorios.items()},
        "comparacao": comparacao,
    }
    evidence.write("quality.json", payload)

    # Relatório legível ao lado do JSON: quem lê a decisão não deveria precisar
    # abrir um JSON para saber o que aconteceu.
    markdown = [
        "# Qualidade com ground truth atrasado — Bora Fibra · churn-v1",
        "",
        f"Medido em {payload['medido_em']} · limiar de decisão {limiar_decisao}",
        "",
        "| Janela | F1 | ROC-AUC | Precisão | Recall | Churn previsto | Churn real |",
        "|---|---|---|---|---|---|---|",
    ]
    for chave, r in relatorios.items():
        markdown.append(
            f"| {chave} | {r.f1:.4f} | {r.roc_auc:.4f} | {r.precision:.4f} | "
            f"{r.recall:.4f} | {r.predicted_churn_rate:.4f} | {r.actual_churn_rate:.4f} |"
        )
    markdown += [
        "",
        "## Matriz de confusão",
        "",
        "| Janela | Verdadeiro negativo | Falso positivo | Falso negativo | Verdadeiro positivo |",
        "|---|---|---|---|---|",
    ]
    for chave, r in relatorios.items():
        markdown.append(
            f"| {chave} | {r.true_negatives} | {r.false_positives} | "
            f"{r.false_negatives} | {r.true_positives} |"
        )
    markdown += [
        "",
        "## Leitura",
        "",
        f"- Queda de F1: **{comparacao['queda_f1']:.4f}** ({comparacao['f1_referencia']:.4f} -> {comparacao['f1_observado']:.4f})",
        f"- Queda de ROC-AUC: **{comparacao['queda_roc_auc']:.4f}**",
        f"- Modo de falha na janela com drift: {comparacao['modo_de_falha']}",
        f"- {comparacao['leitura']}",
        "",
        "O drift foi detectado antes desta medição. Esta medição é o que transforma",
        "o sinal em evidência de perda de qualidade — e é ela, não o PSI, que sustenta",
        "uma decisão sobre retraining.",
        "",
    ]
    (config.EVIDENCE_DIR / "quality.md").write_text("\n".join(markdown), encoding="utf-8")

    emit(payload)
    return 0


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #


def cmd_evidence(_: argparse.Namespace) -> int:
    cfg = config.load_config()
    outputs = aws.terraform_outputs()
    endpoint = outputs.get("endpoint_name") or ""
    if not endpoint:
        raise LabError("o estágio de serving não foi aplicado. Rode `make apply`.")

    log("[evidence] consultando o estado real dos recursos")
    descricao = aws.client("sagemaker").describe_endpoint(EndpointName=endpoint)
    recursos = {
        "endpoint_name": endpoint,
        "endpoint_status": descricao["EndpointStatus"],
        "endpoint_config_name": outputs.get("endpoint_config_name"),
        "model_name": outputs.get("model_name"),
        "model_lineage": outputs.get("model_lineage"),
        "model_artifact_uri": outputs.get("model_artifact_uri"),
        "training_job_name": outputs.get("training_job_name"),
        "bucket_name": outputs.get("bucket_name"),
        "alarm_name": outputs.get("alarm_name"),
        "dashboard_name": outputs.get("dashboard_name"),
        "event_rule_name": outputs.get("event_rule_name"),
        "lambda_function_name": outputs.get("lambda_function_name"),
        "instance_type": outputs.get("instance_type"),
    }
    evidence.write("resource-status.json", recursos)

    log("[evidence] contando datapoints no CloudWatch")
    namespace_ml = monitoring.namespace()

    def soma_sagemaker(metrica: str) -> int:
        """Soma de uma métrica nativa do endpoint na última hora.

        Devolve inteiro: invocação e erro HTTP são contagem, e "40.0 invocações" num
        relatório lido por área de negócio parece defeito.
        """
        resposta = aws.client("cloudwatch").get_metric_statistics(
            Namespace="AWS/SageMaker",
            MetricName=metrica,
            Dimensions=[
                {"Name": "EndpointName", "Value": endpoint},
                {"Name": "VariantName", "Value": "AllTraffic"},
            ],
            StartTime=datetime.now(timezone.utc) - timedelta(hours=1),
            EndTime=datetime.now(timezone.utc),
            Period=60,
            Statistics=["Sum"],
        )
        return int(sum(p["Sum"] for p in resposta.get("Datapoints", [])))

    cloudwatch = {
        "invocations": soma_sagemaker("Invocations"),
        "invocation_4xx": soma_sagemaker("Invocation4XXErrors"),
        "invocation_5xx": soma_sagemaker("Invocation5XXErrors"),
        "data_drift_datapoints": monitoring.metric_datapoint_count(
            namespace_ml, "DataDriftPSIMax", endpoint
        ),
        "prediction_drift_datapoints": monitoring.metric_datapoint_count(
            namespace_ml, "PredictionDriftPSI", endpoint
        ),
        "reaction_triggered_datapoints": monitoring.metric_datapoint_count(
            namespace_ml, "ReactionTriggered", endpoint
        ),
        "namespace": namespace_ml,
    }
    evidence.write("cloudwatch.json", cloudwatch)

    painel = monitoring.dashboard_summary(outputs["dashboard_name"])
    painel["url"] = outputs.get("dashboard_url")
    evidence.write("dashboard.json", painel)

    bundle = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "conta": outputs.get("account_id"),
        "regiao": outputs.get("region"),
        "linhagem": cfg["lineage"],
        "recursos": recursos,
        "cloudwatch": cloudwatch,
        "dashboard": painel,
        "baseline_drift": evidence.read("baseline-drift.json"),
        "production_drift": evidence.read("production-drift.json"),
        "alarme": evidence.read("alarm.json"),
        "reacao": evidence.read("reaction.json"),
        "qualidade": evidence.read("quality.json"),
    }

    manifesto = evidence.build_manifest(bundle)
    evidence.write("manifest.json", manifesto)
    (config.EVIDENCE_DIR / "evidence.md").write_text(
        evidence.render_markdown(bundle), encoding="utf-8"
    )

    faltando = [
        arquivo
        for arquivo, info in manifesto["arquivos_por_etapa"].items()
        if not info["existe"]
    ]
    log("")
    log(f"[evidence] dossiê escrito em {config.EVIDENCE_DIR}")
    for caminho in sorted(config.EVIDENCE_DIR.iterdir()):
        log(f"  {caminho.name}")
    if faltando:
        log("")
        log(f"[aviso] etapas ausentes no dossiê: {', '.join(faltando)}")

    emit({"evidence_dir": str(config.EVIDENCE_DIR), "manifest": manifesto})
    return 0


# --------------------------------------------------------------------------- #
# resumo
# --------------------------------------------------------------------------- #

INICIO_EVIDENCIAS = "<!-- inicio-evidencias -->"
FIM_EVIDENCIAS = "<!-- fim-evidencias -->"


def _num(valor: float | None, digits: int = 2) -> str:
    """Número em português de gente: vírgula decimal, sem casas falsas.

    O PSI e o F1 chegam com seis casas decimais no JSON (precisão do cálculo, não
    da leitura). Duas casas bastam para qualquer decisão deste lab, e a vírgula é
    o separador decimal em português — ponto aqui pareceria milhar.
    """
    if valor is None:
        return "não medido"
    return f"{valor:.{digits}f}".replace(".", ",")


def _pct(fracao: float | None) -> str:
    """Fração para porcentagem redonda: 0.895 -> 90%. Decimal de fração não ajuda decisão."""
    if fracao is None:
        return "não medido"
    return f"{round(fracao * 100)}%"


def _frases_evidencia(
    baseline: dict | None,
    drift_: dict | None,
    alarme: dict | None,
    reacao: dict | None,
    qualidade: dict | None,
) -> dict[str, str]:
    """Uma frase por linha da tabela, escrita para quem não abre JSON."""
    frases: dict[str, str] = {}

    if baseline:
        frases["baseline"] = (
            f"Na janela saudável, o PSI máximo ficou em **{_num(baseline.get('psi_max'))}** "
            f"(limiar {_num(baseline.get('limiar'))}), e o alarme continuou "
            f"**{baseline.get('alarme_apos_baseline', '?')}** — a régua não disparou sem motivo."
        )

    if drift_:
        n_acima = len(drift_.get("features_acima_do_limiar") or [])
        frases["drift"] = (
            f"Na janela com drift, o PSI máximo subiu para **{_num(drift_.get('psi_max'))}**, "
            f"puxado por **`{drift_.get('feature_com_maior_psi', '?')}`**; "
            f"**{n_acima} de 7 variáveis** passaram do limiar de {_num(drift_.get('limiar'))}. "
            f"O PSI das predições foi **{_num(drift_.get('prediction_psi'))}**, com "
            f"**{_pct(drift_.get('predicted_churn_rate'))}** dos clientes previstos como churn."
        )

    if alarme:
        frases["alarme"] = (
            f"O alarme foi para **{alarme.get('estado', '?')}**. Motivo registrado pelo "
            f"CloudWatch: \"{evidence._resumir(alarme.get('motivo', ''))}\". "
            f"({len(alarme.get('historico') or [])} transição(ões) recente(s) no histórico.)"
        )

    if reacao:
        incidente = reacao.get("incidente") or {}
        frases["reacao"] = (
            f"A Lambda rodou **{reacao.get('invocacoes_no_log', '?')}** vez(es) e escreveu o "
            f"incidente em `{reacao.get('objeto_s3', '?')}`, registrando "
            f"**{incidente.get('status', '?')} / {incidente.get('recommended_action', '?')}**. "
            f"Nenhum treinamento novo foi disparado "
            f"(**{reacao.get('training_jobs_criados_depois', '?')}** job(s)) — a reação não "
            "retreina sozinha."
        )

    if qualidade:
        comparacao = qualidade.get("comparacao") or {}
        frases["qualidade"] = (
            f"Com o rótulo verdadeiro, o F1 caiu de **{_num(comparacao.get('f1_referencia'))}** "
            f"para **{_num(comparacao.get('f1_observado'))}** "
            f"(queda de {_num(comparacao.get('queda_f1'))}), e o ROC-AUC caiu "
            f"**{_num(comparacao.get('queda_roc_auc'))}**. Modo de falha na janela com drift: "
            f"{comparacao.get('modo_de_falha', '?')}"
        )

    return frases


def _grava_evidencias_no_decision(
    baseline: dict | None,
    drift_: dict | None,
    alarme: dict | None,
    reacao: dict | None,
    qualidade: dict | None,
) -> int:
    """Reescreve a tabela de evidências do DECISION.md com os números medidos.

    Só o bloco entre os marcadores é trocado: o que o aluno escreveu nas seções de
    evidência e recomendação fica intacto, e rodar de novo não duplica nada. Se o
    arquivo não tiver os marcadores (aluno apagou sem querer), avisa e não mexe —
    perder o texto que ele escreveu seria muito pior que deixar a tabela desatualizada.
    """
    caminho = config.LAB_ROOT / "DECISION.md"
    if not caminho.exists():
        log("aviso: DECISION.md não encontrado; a tabela não foi atualizada")
        return 0

    texto = caminho.read_text(encoding="utf-8")
    if INICIO_EVIDENCIAS not in texto or FIM_EVIDENCIAS not in texto:
        log("aviso: os marcadores de evidência não estão no DECISION.md; a tabela não foi atualizada")
        return 0

    frases = _frases_evidencia(baseline, drift_, alarme, reacao, qualidade)
    pendente = {
        "baseline": "_rode `make baseline`_",
        "drift": "_rode `make drift`_",
        "alarme": "_rode `make alarm-status`_",
        "reacao": "_rode `make reaction`_",
        "qualidade": "_rode `make ground-truth`_",
    }
    rotulos = [
        ("baseline", "Janela saudável", "make baseline"),
        ("drift", "Janela com drift", "make drift"),
        ("alarme", "Alarme de drift", "make alarm-status"),
        ("reacao", "Reação automática", "make reaction"),
        ("qualidade", "Qualidade com ground truth", "make ground-truth"),
    ]

    bloco = [
        INICIO_EVIDENCIAS,
        "| Sinal | Etapa | O que medimos na sua execução |",
        "|---|---|---|",
        *[f"| {nome} | {etapa} | {frases.get(chave, pendente[chave])} |" for chave, nome, etapa in rotulos],
        FIM_EVIDENCIAS,
    ]

    caminho.write_text(
        texto[: texto.index(INICIO_EVIDENCIAS)]
        + "\n".join(bloco)
        + texto[texto.index(FIM_EVIDENCIAS) + len(FIM_EVIDENCIAS) :],
        encoding="utf-8",
    )
    return sum(1 for chave, _, _ in rotulos if chave in frases)


def cmd_resumo(_: argparse.Namespace) -> int:
    """Escreve a tabela de evidências no DECISION.md e repete no terminal.

    As mesmas frases vão para os dois lugares, de propósito: se o terminal dissesse
    uma coisa e o documento outra, o aluno não saberia em qual confiar.
    """
    baseline = evidence.read("baseline-drift.json")
    drift_ = evidence.read("production-drift.json")
    alarme = evidence.read("alarm.json")
    reacao = evidence.read("reaction.json")
    qualidade = evidence.read("quality.json")

    frases = _frases_evidencia(baseline, drift_, alarme, reacao, qualidade)
    secoes = [
        ("baseline", "JANELA SAUDÁVEL — baseline", "make baseline"),
        ("drift", "JANELA COM DRIFT", "make drift"),
        ("alarme", "ALARME DE DRIFT", "make alarm-status"),
        ("reacao", "REAÇÃO AUTOMÁTICA", "make reaction"),
        ("qualidade", "QUALIDADE COM GROUND TRUTH", "make ground-truth"),
    ]

    falta: list[str] = []
    for chave, titulo, comando in secoes:
        log(titulo)
        if chave in frases:
            # O negrito do markdown não ajuda no terminal; sai só no documento.
            log("  " + frases[chave].replace("**", ""))
        else:
            log(f"  ainda não medido — rode `{comando}`")
            falta.append(comando)
        log("")

    escritas = _grava_evidencias_no_decision(baseline, drift_, alarme, reacao, qualidade)
    log(f"DECISION.md: tabela de evidências atualizada ({escritas} de 5 linhas com dado medido).")
    if falta:
        log("Ainda não medido: " + ", ".join(sorted(set(falta))))
    log("O que resta no arquivo é só a sua decisão — a tabela é regravada a cada `make resumo`.")

    emit(
        {
            "baseline": baseline,
            "drift": drift_,
            "alarme": alarme,
            "reacao": reacao,
            "qualidade": qualidade,
        }
    )
    return 0


# --------------------------------------------------------------------------- #
# verify-clean
# --------------------------------------------------------------------------- #


def cmd_verify_clean(_: argparse.Namespace) -> int:
    """Prova por API que nada cobrado sobrou. O state do Terraform não é autoridade.

    Por que não confiar no state: `terraform destroy` pode ter falhado no meio, o
    state pode ter sido apagado, ou um recurso pode ter nascido fora do Terraform.
    A única prova é perguntar a cada serviço se o recurso ainda está lá — e é por
    isso que este comando reconstrói o prefixo a partir do config, não dos outputs.
    """
    cfg = config.load_config()
    prefixo = cfg["aws"]["bucket_prefix"]
    checks = Checks()

    sagemaker = aws.client("sagemaker")

    endpoints = [
        e["EndpointName"]
        for e in sagemaker.list_endpoints(NameContains=prefixo, MaxResults=100).get(
            "Endpoints", []
        )
    ]
    checks.add(
        "no_endpoint",
        not endpoints,
        "nenhum endpoint com o prefixo do lab"
        if not endpoints
        else f"AINDA COBRANDO: {', '.join(endpoints)}",
    )

    configs = [
        c["EndpointConfigName"]
        for c in sagemaker.list_endpoint_configs(NameContains=prefixo, MaxResults=100).get(
            "EndpointConfigs", []
        )
    ]
    checks.add(
        "no_endpoint_config",
        not configs,
        "nenhum endpoint config" if not configs else f"restaram: {', '.join(configs)}",
    )

    modelos = [
        m["ModelName"]
        for m in sagemaker.list_models(NameContains=prefixo, MaxResults=100).get("Models", [])
    ]
    checks.add(
        "no_model", not modelos, "nenhum model" if not modelos else f"restaram: {', '.join(modelos)}"
    )

    em_execucao = [
        j["TrainingJobName"]
        for j in sagemaker.list_training_jobs(
            NameContains=prefixo, StatusEquals="InProgress", MaxResults=100
        ).get("TrainingJobSummaries", [])
    ]
    checks.add(
        "no_training_job_running",
        not em_execucao,
        "nenhum training job em execução"
        if not em_execucao
        else f"AINDA COBRANDO: {', '.join(em_execucao)}",
    )

    alarmes = [
        a["AlarmName"]
        for a in aws.client("cloudwatch")
        .describe_alarms(AlarmNamePrefix=prefixo, MaxRecords=100)
        .get("MetricAlarms", [])
    ]
    checks.add(
        "no_alarm", not alarmes, "nenhum alarme" if not alarmes else f"restaram: {', '.join(alarmes)}"
    )

    # O dashboard é o recurso que um verify-clean ingênuo esquece: ele não aparece em
    # nenhuma listagem de compute, e continua existindo (e custando) depois de o
    # endpoint morrer.
    paineis = [
        d["DashboardName"]
        for d in aws.client("cloudwatch")
        .list_dashboards(DashboardNamePrefix="fiap-mlops-")
        .get("DashboardEntries", [])
    ]
    checks.add(
        "no_dashboard",
        not paineis,
        "nenhum dashboard fiap-mlops-*"
        if not paineis
        else f"restaram: {', '.join(paineis)}",
    )

    regras = [
        r["Name"]
        for r in aws.client("events").list_rules(NamePrefix=prefixo, Limit=100).get("Rules", [])
    ]
    checks.add(
        "no_event_rule",
        not regras,
        "nenhuma regra do EventBridge" if not regras else f"restaram: {', '.join(regras)}",
    )

    funcoes = [
        f["FunctionName"]
        for pagina in aws.client("lambda").get_paginator("list_functions").paginate()
        for f in pagina.get("Functions", [])
        if f["FunctionName"].startswith(prefixo)
    ]
    checks.add(
        "no_lambda",
        not funcoes,
        "nenhuma função Lambda" if not funcoes else f"restaram: {', '.join(funcoes)}",
    )

    grupos = [
        g["logGroupName"]
        for g in aws.client("logs")
        .describe_log_groups(logGroupNamePrefix=f"/aws/lambda/{prefixo}")
        .get("logGroups", [])
    ]
    checks.add(
        "no_log_group",
        not grupos,
        "nenhum log group da Lambda" if not grupos else f"restaram: {', '.join(grupos)}",
    )

    buckets = [
        b["Name"]
        for b in aws.client("s3").list_buckets().get("Buckets", [])
        if b["Name"].startswith(prefixo)
    ]
    checks.add(
        "no_bucket",
        not buckets,
        "nenhum bucket do lab" if not buckets else f"restaram: {', '.join(buckets)}",
    )

    resultado = checks.summary("verificação de limpeza")
    emit(resultado)
    return 0 if resultado["passed"] else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

COMANDOS: dict[str, tuple[Callable[[argparse.Namespace], int], str]] = {
    "doctor": (cmd_doctor, "Confere ferramentas, credencial, região, role e serviços"),
    "data": (cmd_data, "Gera o dataset determinístico"),
    "validate-data": (cmd_validate_data, "Roda o contrato de dados e imprime os hashes"),
    "wait-training": (cmd_wait_training, "Espera o training job e resolve o artefato"),
    "status": (cmd_status, "Descreve endpoint, alarme, dashboard, Lambda e regra"),
    "dashboard": (cmd_dashboard, "Imprime nome e link direto do dashboard"),
    "baseline": (cmd_baseline, "Observa a janela saudável e prova que o alarme está OK"),
    "drift": (cmd_drift, "Observa a janela deslocada e publica o drift"),
    "alarm-status": (cmd_alarm_status, "Mostra o estado do alarme e o histórico"),
    "reaction": (cmd_reaction, "Espera o incidente da Lambda e valida o conteúdo"),
    "ground-truth": (cmd_ground_truth, "Avalia a qualidade com o rótulo atrasado"),
    "evidence": (cmd_evidence, "Consolida o dossiê de evidência"),
    "resumo": (cmd_resumo, "Escreve a tabela de evidências no DECISION.md"),
    "verify-clean": (cmd_verify_clean, "Prova por API que nada cobrado sobrou"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lab.py", description="Lab 04.1 — observabilidade, drift e resposta operacional"
    )
    sub = parser.add_subparsers(dest="comando", required=True)
    for nome, (_, ajuda) in COMANDOS.items():
        filho = sub.add_parser(nome, help=ajuda)
        if nome == "alarm-status":
            filho.add_argument(
                "--wait-for-alarm",
                action="store_true",
                help="espera a transição para ALARM em vez de só consultar o estado",
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    funcao, _ = COMANDOS[args.comando]
    try:
        return funcao(args)
    except LabError as exc:
        log("")
        log(f"{FAIL} {exc}")
        return 1
    except KeyboardInterrupt:
        log("")
        log("[aviso] interrompido. Recursos criados continuam de pé: rode `make destroy`.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
