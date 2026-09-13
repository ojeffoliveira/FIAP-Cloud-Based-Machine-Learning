# Autoscaling do endpoint V2 apenas. V1 fica com capacidade fixa 1 (sem nenhum
# recurso aqui) — decisão explícita da missão: V1 é o baseline estável do
# Codespaces, V2 é onde a aula demonstra target tracking.
#
# ALVO DE AUTOSCALING — raciocínio (não pode ser chute, missão A6b):
#
# Fato medido pelo A2 no probe real (ml.m5.xlarge, 64 tokens de saída,
# runtime.json): p50 = 1,90 s, p95 = 1,96 s por invocação.
#
# O llama.cpp server é CPU-bound numa instância de 4 vCPU: mais requisições
# concorrentes não paralelizam de forma limpa, elas competem pelo mesmo CPU e a
# latência de cada uma degrada. Por isso o pior caso (p95) é usado como base do
# throughput sustentável, não uma média otimista:
#
#   throughput_max_serializado = 60s / 1,96s ≈ 30,6 invocações/min/instância
#   (limite teórico se cada requisição fosse atendida sem nenhuma fila)
#
# SageMakerVariantInvocationsPerInstance é avaliada pela Application Auto
# Scaling em janelas de ~3 minutos (médias móveis), então o alvo precisa de
# margem para (a) não estourar latência ANTES do scale-out completar
# (provisionar uma nova instância ml.m5.xlarge leva minutos, não segundos) e
# (b) não oscilar com o ruído normal de uma demo de sala de aula.
#
# Margem de segurança aplicada: ~65% do throughput máximo teórico.
#   target_value ≈ 30,6 * 0,65 ≈ 19,9  →  20 invocações/instância/minuto
#
# Em outras palavras: o alvo dispara o scale-out quando a instância está
# processando, em média, uma invocação a cada ~3s — bem acima do p95 medido de
# 1,96s por requisição, então há folga real antes do endpoint saturar.
locals {
  autoscaling_min_capacity       = 1
  autoscaling_max_capacity       = 2
  autoscaling_target_invocations = 20
  # Cooldowns assimétricos de propósito (mesmo princípio de design do
  # 04.1/monitoring.tf: reagir rápido a um problema, ser conservador para
  # desligar). Scale-out curto porque uma nova instância já demora minutos
  # para ficar InService — não adianta esperar mais para pedir; scale-in mais
  # longo evita destruir e recriar capacidade por uma queda passageira de
  # tráfego (comum entre blocos de uma aula).
  autoscaling_scale_out_cooldown_s = 60
  autoscaling_scale_in_cooldown_s  = 300
}

resource "aws_appautoscaling_target" "v2" {
  count = var.enable_v2 ? 1 : 0

  service_namespace  = "sagemaker"
  resource_id        = "endpoint/${aws_sagemaker_endpoint.v2[0].name}/variant/${local.releases.v2.variant_name}"
  scalable_dimension = "sagemaker:variant:DesiredInstanceCount"
  min_capacity       = local.autoscaling_min_capacity
  max_capacity       = local.autoscaling_max_capacity
}

resource "aws_appautoscaling_policy" "v2_invocations_per_instance" {
  count = var.enable_v2 ? 1 : 0

  name               = "${local.project_prefix}-slm-v2-target-tracking-${local.suffix}"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.v2[0].service_namespace
  resource_id        = aws_appautoscaling_target.v2[0].resource_id
  scalable_dimension = aws_appautoscaling_target.v2[0].scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "SageMakerVariantInvocationsPerInstance"
    }

    target_value       = local.autoscaling_target_invocations
    scale_out_cooldown = local.autoscaling_scale_out_cooldown_s
    scale_in_cooldown  = local.autoscaling_scale_in_cooldown_s
  }
}
