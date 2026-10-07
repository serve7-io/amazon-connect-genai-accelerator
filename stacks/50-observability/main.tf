# Layer 50 - observability: SNS topic, alarm baseline (guide 4.5), tenant dashboard.

locals {
  layer   = "50-observability"
  f       = data.terraform_remote_state.foundation.outputs
  c       = data.terraform_remote_state.core.outputs
  g       = data.terraform_remote_state.genai.outputs
  gw_name = local.g.gateway_lambda_name
}

data "terraform_remote_state" "foundation" {
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "10-foundation/terraform.tfstate", region = var.region }
}

data "terraform_remote_state" "core" {
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "20-connect-core/terraform.tfstate", region = var.region }
}

data "terraform_remote_state" "genai" {
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "40-genai/terraform.tfstate", region = var.region }
}

variable "alarm_emails" {
  type    = list(string)
  default = []
}

resource "aws_sns_topic" "alarms" {
  name              = "${local.prefix}-alarms"
  kms_master_key_id = local.f.kms_logs_arn
  tags              = { "serve7:component" = "alarms" }
}

resource "aws_sns_topic_subscription" "email" {
  for_each  = toset(var.alarm_emails)
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = each.value
}

locals {
  alarms = {
    connect-throttled-calls = {
      namespace = "AWS/Connect", metric = "ThrottledCalls", stat = "Sum", threshold = 0, periods = 1
      dims      = { InstanceId = local.c.instance_id, MetricGroup = "VoiceCalls" }
      desc      = "Calls rejected because the instance hit its concurrency limit."
    }
    connect-concurrency-breach = {
      namespace = "AWS/Connect", metric = "CallsBreachingConcurrencyQuota", stat = "Sum", threshold = 0, periods = 1
      dims      = { InstanceId = local.c.instance_id, MetricGroup = "VoiceCalls" }
      desc      = "Calls above the concurrent-calls quota."
    }
    gateway-errors = {
      namespace = "AWS/Lambda", metric = "Errors", stat = "Sum", threshold = 5, periods = 1
      dims      = { FunctionName = local.gw_name }
      desc      = "GenAI gateway Lambda errors."
    }
    gateway-throttles = {
      namespace = "AWS/Lambda", metric = "Throttles", stat = "Sum", threshold = 0, periods = 1
      dims      = { FunctionName = local.gw_name }
      desc      = "GenAI gateway Lambda throttled; callers fall back to agents."
    }
    gateway-fallbacks = {
      namespace = "Serve7/GenAIGateway", metric = "Fallback", stat = "Sum", threshold = 10, periods = 1
      dims      = { Tenant = var.tenant }
      desc      = "Bedrock throttling/timeouts forcing agent fallback."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each            = local.alarms
  alarm_name          = "${local.prefix}-${each.key}"
  alarm_description   = each.value.desc
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.stat
  dimensions          = each.value.dims
  period              = 300
  evaluation_periods  = each.value.periods
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  tags                = { "serve7:component" = "alarm" }
}

resource "aws_cloudwatch_metric_alarm" "gateway_latency" {
  alarm_name          = "${local.prefix}-gateway-p95-latency"
  alarm_description   = "GenAI gateway p95 above 5 s (Connect limit is 8 s)."
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  extended_statistic  = "p95"
  dimensions          = { FunctionName = local.gw_name }
  period              = 300
  evaluation_periods  = 2
  threshold           = 5000
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  tags                = { "serve7:component" = "alarm" }
}

resource "aws_cloudwatch_dashboard" "tenant" {
  dashboard_name = "${local.prefix}-overview"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title  = "Connect calls"
          region = var.region
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Connect", "CallsPerInterval", "InstanceId", local.c.instance_id, "MetricGroup", "VoiceCalls"],
            [".", "MissedCalls", ".", ".", ".", "."],
            [".", "ThrottledCalls", ".", ".", ".", "."]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title  = "GenAI gateway"
          region = var.region
          period = 300
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", local.gw_name, { stat = "Sum" }],
            [".", "Errors", ".", ".", { stat = "Sum" }],
            [".", "Duration", ".", ".", { stat = "p95" }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 24, height = 6
        properties = {
          title  = "AI outcomes"
          region = var.region
          stat   = "Sum"
          period = 300
          metrics = [
            ["Serve7/GenAIGateway", "GuardrailIntervened", "Tenant", var.tenant],
            [".", "Fallback", ".", "."],
            [".", "InputTokens", ".", "."],
            [".", "OutputTokens", ".", "."]
          ]
        }
      }
    ]
  })
}

output "alarm_topic_arn" {
  value = aws_sns_topic.alarms.arn
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.tenant.dashboard_name
}
