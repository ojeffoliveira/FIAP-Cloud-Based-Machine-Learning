# Application Auto Scaling. O real-time recebe target tracking clássico (1-2
# instâncias por invocações/instância/min). O async recebe target tracking de 0-1
# sobre o backlog, mais uma política de step scaling ligada ao alarme
# HasBacklogWithoutCapacity do CloudWatch, porque target tracking sozinho não
# consegue escalar uma variant que já está com zero instância para medir
# invocações-por-instância.

# --------------------------------------------------------------------------- #
# Real-Time: 1-2, SageMakerVariantInvocationsPerInstance
# --------------------------------------------------------------------------- #

resource "aws_appautoscaling_target" "realtime" {
  count = var.deploy_serving ? 1 : 0

  service_namespace  = "sagemaker"
  resource_id        = "endpoint/${aws_sagemaker_endpoint.realtime[0].name}/variant/AllTraffic"
  scalable_dimension = "sagemaker:variant:DesiredInstanceCount"
  min_capacity       = var.realtime_min_capacity
  max_capacity       = var.realtime_max_capacity
}

resource "aws_appautoscaling_policy" "realtime_target_tracking" {
  count = var.deploy_serving ? 1 : 0

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
# Async: 0-1, ApproximateBacklogSizePerInstance + scale-from-zero
# --------------------------------------------------------------------------- #

resource "aws_appautoscaling_target" "async" {
  count = var.deploy_serving ? 1 : 0

  service_namespace  = "sagemaker"
  resource_id        = "endpoint/${aws_sagemaker_endpoint.async[0].name}/variant/AllTraffic"
  scalable_dimension = "sagemaker:variant:DesiredInstanceCount"
  min_capacity       = var.async_min_capacity
  max_capacity       = var.async_max_capacity
}

resource "aws_appautoscaling_policy" "async_target_tracking" {
  count = var.deploy_serving ? 1 : 0

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

# Escalar a partir de zero: uma política de step scaling que um alarme do
# CloudWatch em HasBacklogWithoutCapacity dispara, seguindo o padrão de
# autoscaling assíncrono documentado pela AWS (target tracking sozinho nunca
# dispara com capacidade 0).
resource "aws_appautoscaling_policy" "async_scale_from_zero" {
  count = var.deploy_serving ? 1 : 0

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
  count = var.deploy_serving ? 1 : 0

  alarm_name          = "${var.project_prefix}-async-backlog-no-capacity-${local.suffix}"
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
