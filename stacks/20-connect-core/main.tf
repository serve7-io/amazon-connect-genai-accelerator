# Layer 20 - Amazon Connect instance, storage, streaming.

locals {
  layer = "20-connect-core"
  f     = data.terraform_remote_state.foundation.outputs
}

data "terraform_remote_state" "foundation" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "10-foundation/terraform.tfstate"
    region = var.region
  }
}

variable "kinesis_stream_mode" {
  description = "PROVISIONED (1 shard, ~$0.40/day per stream) for sandboxes; ON_DEMAND for production traffic."
  type        = string
  default     = "ON_DEMAND"
  validation {
    condition     = contains(["ON_DEMAND", "PROVISIONED"], var.kinesis_stream_mode)
    error_message = "kinesis_stream_mode must be ON_DEMAND or PROVISIONED."
  }
}

variable "instance_alias" {
  description = "Globally unique within the region; becomes https://<alias>.my.connect.aws"
  type        = string
}

resource "aws_connect_instance" "this" {
  instance_alias                   = var.instance_alias
  identity_management_type         = "CONNECT_MANAGED"
  inbound_calls_enabled            = true
  outbound_calls_enabled           = true
  contact_flow_logs_enabled        = true
  contact_lens_enabled             = true
  auto_resolve_best_voices_enabled = true
  early_media_enabled              = true

  tags = { "serve7:component" = "connect-instance" }

  lifecycle {
    prevent_destroy = false # true for prod tenants (override file)
  }
}

# ------------------------------------------------------------ recordings + transcripts
resource "aws_connect_instance_storage_config" "call_recordings" {
  instance_id   = aws_connect_instance.this.id
  resource_type = "CALL_RECORDINGS"
  storage_config {
    storage_type = "S3"
    s3_config {
      bucket_name   = local.f.recordings_bucket
      bucket_prefix = "recordings"
      encryption_config {
        encryption_type = "KMS"
        key_id          = local.f.kms_connect_arn
      }
    }
  }
}

resource "aws_connect_instance_storage_config" "chat_transcripts" {
  instance_id   = aws_connect_instance.this.id
  resource_type = "CHAT_TRANSCRIPTS"
  storage_config {
    storage_type = "S3"
    s3_config {
      bucket_name   = local.f.recordings_bucket
      bucket_prefix = "chat-transcripts"
      encryption_config {
        encryption_type = "KMS"
        key_id          = local.f.kms_connect_arn
      }
    }
  }
}

resource "aws_connect_instance_storage_config" "scheduled_reports" {
  instance_id   = aws_connect_instance.this.id
  resource_type = "SCHEDULED_REPORTS"
  storage_config {
    storage_type = "S3"
    s3_config {
      bucket_name   = local.f.recordings_bucket
      bucket_prefix = "reports"
      encryption_config {
        encryption_type = "KMS"
        key_id          = local.f.kms_connect_arn
      }
    }
  }
}

# ------------------------------------------------------------ streaming (CTR + agent events)
resource "aws_kinesis_stream" "ctr" {
  name             = "${local.prefix}-ctr"
  retention_period = 24
  shard_count      = var.kinesis_stream_mode == "PROVISIONED" ? 1 : null
  encryption_type  = "KMS"
  kms_key_id       = local.f.kms_connect_arn
  stream_mode_details {
    stream_mode = var.kinesis_stream_mode
  }
  tags = { "serve7:component" = "ctr-stream" }
}

resource "aws_kinesis_stream" "agent_events" {
  name             = "${local.prefix}-agent-events"
  retention_period = 24
  shard_count      = var.kinesis_stream_mode == "PROVISIONED" ? 1 : null
  encryption_type  = "KMS"
  kms_key_id       = local.f.kms_connect_arn
  stream_mode_details {
    stream_mode = var.kinesis_stream_mode
  }
  tags = { "serve7:component" = "agent-event-stream" }
}

resource "aws_connect_instance_storage_config" "ctr" {
  instance_id   = aws_connect_instance.this.id
  resource_type = "CONTACT_TRACE_RECORDS"
  storage_config {
    storage_type = "KINESIS_STREAM"
    kinesis_stream_config {
      stream_arn = aws_kinesis_stream.ctr.arn
    }
  }
}

resource "aws_connect_instance_storage_config" "agent_events" {
  instance_id   = aws_connect_instance.this.id
  resource_type = "AGENT_EVENTS"
  storage_config {
    storage_type = "KINESIS_STREAM"
    kinesis_stream_config {
      stream_arn = aws_kinesis_stream.agent_events.arn
    }
  }
}

output "instance_id" {
  value = aws_connect_instance.this.id
}

output "instance_arn" {
  value = aws_connect_instance.this.arn
}

output "instance_alias" {
  value = aws_connect_instance.this.instance_alias
}

output "ctr_stream_arn" {
  value = aws_kinesis_stream.ctr.arn
}

output "agent_events_stream_arn" {
  value = aws_kinesis_stream.agent_events.arn
}

output "access_url" {
  value = "https://${aws_connect_instance.this.instance_alias}.my.connect.aws"
}
