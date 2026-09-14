# Application Auto Scaling, mesmo padrao comprovado no lab 03
# (03-serving-and-scaling/terraform/autoscaling.tf, ver ACADEMY.md item 7):
# Real-Time recebe target tracking classico (1-2 instancias por
# invocacoes/instancia/min). Async recebe target tracking de 0-1 sobre o
# backlog, mais uma politica de step scaling ligada a um alarme
# HasBacklogWithoutCapacity -- target tracking sozinho nunca escala uma
# variant que ja esta com zero instancia, porque nao ha invocacao-por-
# instancia para medir nesse estado.
#
# Serverless nao entra aqui: a AWS gerencia a capacidade dela, sem
# Application Auto Scaling (é exatamente a troca que o candidato oferece).

# --------------------------------------------------------------------------- #
# Real-Time: min/max de var.realtime_min_capacity/max_capacity,
# SageMakerVariantInvocationsPerInstance
# --------------------------------------------------------------------------- #

resource "aws_appautoscaling_target" "realtime" {
  count = var.enable_serving ? 1 : 0

  service_namespace  = "sagemaker"
  resource_id        = "endpoint/${aws_sagemaker_endpoint.realtime[0].name}/variant/AllTraffic"
  scalable_dimension = "sagemaker:variant:DesiredInstanceCount"
  min_capacity       = var.realtime_min_capacity
  max_capacity       = var.realtime_max_capacity
}

resource "aws_appautoscaling_policy" "realtime_target_tracking" {
  count = var.enable_serving ? 1 : 0

  name               = local.realtime_scaling_policy_name
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.realtime[0].service_namespace
  resource_id        = aws_appautoscaling_target.realtime[0].resource_id
  scalable_dimension = aws_appautoscaling_target.realtime[0].scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "SageMakerVariantInvocationsPerInstance"
    }
    target_value       = var.realtime_target_invocations_per_instance
    scale_in_cooldown  = var.realtime_scale_in_cooldown
    scale_out_cooldown = var.realtime_scale_out_cooldown
  }
}

# --------------------------------------------------------------------------- #
# Async: min/max de var.async_min_capacity/max_capacity (0-1 por default),
# ApproximateBacklogSizePerInstance + escala a partir de zero
# --------------------------------------------------------------------------- #

resource "aws_appautoscaling_target" "async" {
  count = var.enable_serving ? 1 : 0

  service_namespace  = "sagemaker"
  resource_id        = "endpoint/${aws_sagemaker_endpoint.async[0].name}/variant/AllTraffic"
  scalable_dimension = "sagemaker:variant:DesiredInstanceCount"
  min_capacity       = var.async_min_capacity
  max_capacity       = var.async_max_capacity
}

resource "aws_appautoscaling_policy" "async_target_tracking" {
  count = var.enable_serving ? 1 : 0

  name               = local.async_scaling_policy_name
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.async[0].service_namespace
  resource_id        = aws_appautoscaling_target.async[0].resource_id
  scalable_dimension = aws_appautoscaling_target.async[0].scalable_dimension

  target_tracking_scaling_policy_configuration {
    customized_metric_specification {
      metric_name = "ApproximateBacklogSizePerInstance"
      namespace   = "AWS/SageMaker"
      statistic   = "Average"
    }
    target_value       = var.async_target_backlog_per_instance
    scale_in_cooldown  = 180
    scale_out_cooldown = 60
  }
}

# Escalar a partir de zero: uma politica de step scaling que um alarme do
# CloudWatch em HasBacklogWithoutCapacity dispara -- target tracking sozinho
# nunca dispara com capacidade 0, porque a metrica que ele observa (invocacoes
# por instancia) nao existe sem instancia.
resource "aws_appautoscaling_policy" "async_scale_from_zero" {
  count = var.enable_serving ? 1 : 0

  name               = "${local.async_scaling_policy_name}-from-zero"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.async[0].service_namespace
  resource_id        = aws_appautoscaling_target.async[0].resource_id
  scalable_dimension = aws_appautoscaling_target.async[0].scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 60
    metric_aggregation_type = "Average"

    step_adjustment {
      scaling_adjustment          = 1
      metric_interval_lower_bound = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "async_has_backlog_without_capacity" {
  count = var.enable_serving ? 1 : 0

  alarm_name          = "${local.prefix}-async-backlog-${local.suffix}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  metric_name         = "HasBacklogWithoutCapacity"
  namespace           = "AWS/SageMaker"
  period              = 60
  statistic           = "Average"
  threshold           = 1
  treat_missing_data  = "missing"

  dimensions = {
    EndpointName = aws_sagemaker_endpoint.async[0].name
    VariantName  = "AllTraffic"
  }

  alarm_actions = [aws_appautoscaling_policy.async_scale_from_zero[0].arn]
}
