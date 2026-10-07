# Layer 40 - GenAI: Bedrock guardrail, model access path, gateway Lambda, contact state, invocation logging.

locals {
  layer = "40-genai"
  f     = data.terraform_remote_state.foundation.outputs
  c     = data.terraform_remote_state.core.outputs
  acct  = data.aws_caller_identity.current.account_id
  part  = data.aws_partition.current.partition

  # The gateway calls the tagged per-tenant application inference profile when one is configured,
  # otherwise the system model / inference profile ID directly.
  invoke_target = var.model_source_arn != "" ? aws_bedrock_inference_profile.tenant[0].arn : var.bedrock_model_id
  invoke_resources = compact(concat(
    var.bedrock_resource_arns,
    var.model_source_arn != "" ? [aws_bedrock_inference_profile.tenant[0].arn, var.model_source_arn] : []
  ))
  guardrail_ref = "${aws_bedrock_guardrail.this.guardrail_arn}:${aws_bedrock_guardrail_version.this.version}"
}

data "terraform_remote_state" "foundation" {
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "10-foundation/terraform.tfstate", region = var.region }
}

data "terraform_remote_state" "core" {
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "20-connect-core/terraform.tfstate", region = var.region }
}

# ============================================================ variables
variable "bedrock_model_id" {
  description = "Model ID or system inference profile ID the gateway invokes (from scripts/preflight.py)."
  type        = string
}

variable "bedrock_resource_arns" {
  description = "Every foundation-model / inference-profile ARN the gateway may invoke (all destination regions)."
  type        = list(string)
}

variable "model_source_arn" {
  description = "Optional: system inference profile or model ARN to wrap in a tagged application inference profile."
  type        = string
  default     = ""
}

variable "enforce_guardrail_iam" {
  description = "Deny any InvokeModel that does not carry the tenant guardrail."
  type        = bool
  default     = true
}

variable "enable_invocation_logging" {
  description = "Bedrock model invocation logging (account+region singleton). Disable for regulated tenants."
  type        = bool
  default     = true
}

variable "denied_topics" {
  type = list(object({
    name       = string
    definition = string
    examples   = list(string)
  }))
  default = [
    {
      name       = "Financial advice"
      definition = "Recommendations about investments, trading, or personal financial decisions."
      examples   = ["Should I buy this stock?", "Where should I invest my savings?"]
    },
    {
      name       = "Medical advice"
      definition = "Diagnosis, treatment or medication guidance for a person's health condition."
      examples   = ["What medicine should I take for chest pain?"]
    },
    {
      name       = "Legal advice"
      definition = "Guidance on a person's specific legal situation or how to act in a legal dispute."
      examples   = ["Can I sue my landlord?"]
    }
  ]
}

variable "contact_state_ttl_days" {
  type    = number
  default = 30
}

# ============================================================ guardrail
resource "aws_bedrock_guardrail" "this" {
  name                      = "${local.prefix}-cx-guardrail"
  description               = "Serve7 CX guardrail for ${var.tenant}/${var.environment}"
  blocked_input_messaging   = "I can't help with that here. Let me connect you with a specialist."
  blocked_outputs_messaging = "I can't share that. Let me connect you with a specialist."
  kms_key_arn               = local.f.kms_data_arn

  content_policy_config {
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
    filters_config {
      type            = "HATE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "INSULTS"
      input_strength  = "MEDIUM"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "MISCONDUCT"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "SEXUAL"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = "HIGH"
      output_strength = "HIGH"
    }
  }

  sensitive_information_policy_config {
    pii_entities_config {
      type   = "CREDIT_DEBIT_CARD_NUMBER"
      action = "BLOCK"
    }
    pii_entities_config {
      type   = "CA_SOCIAL_INSURANCE_NUMBER"
      action = "BLOCK"
    }
    pii_entities_config {
      type   = "EMAIL"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "PHONE"
      action = "ANONYMIZE"
    }
  }

  topic_policy_config {
    dynamic "topics_config" {
      for_each = var.denied_topics
      content {
        name       = topics_config.value.name
        definition = topics_config.value.definition
        examples   = topics_config.value.examples
        type       = "DENY"
      }
    }
  }

  contextual_grounding_policy_config {
    filters_config {
      type      = "GROUNDING"
      threshold = 0.7
    }
    filters_config {
      type      = "RELEVANCE"
      threshold = 0.7
    }
  }

  tags = { "serve7:component" = "bedrock-guardrail", "serve7:genai" = "bedrock" }
}

resource "aws_bedrock_guardrail_version" "this" {
  guardrail_arn = aws_bedrock_guardrail.this.guardrail_arn
  description   = "release ${var.release}"
  skip_destroy  = true

  lifecycle {
    replace_triggered_by = [aws_bedrock_guardrail.this]
  }
}

# ============================================================ per-tenant inference profile (cost allocation)
resource "aws_bedrock_inference_profile" "tenant" {
  count       = var.model_source_arn != "" ? 1 : 0
  name        = "${local.prefix}-gateway"
  description = "Tagged per-tenant profile for Bedrock cost allocation"
  model_source {
    copy_from = var.model_source_arn
  }
  tags = { "serve7:component" = "genai-gateway", "serve7:genai" = "bedrock" }
}

# ============================================================ contact state
resource "aws_dynamodb_table" "contact_state" {
  name         = "${local.prefix}-contact-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "contactId"

  attribute {
    name = "contactId"
    type = "S"
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = local.f.kms_data_arn
  }

  deletion_protection_enabled = var.environment == "prod"

  tags = { "serve7:component" = "contact-state" }
}

# ============================================================ gateway Lambda
data "aws_iam_policy_document" "gateway" {
  statement {
    sid       = "InvokeAllowListedModels"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = local.invoke_resources
  }

  dynamic "statement" {
    for_each = var.enforce_guardrail_iam ? [1] : []
    content {
      sid       = "DenyInvokeWithoutTenantGuardrail"
      effect    = "Deny"
      actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      resources = ["*"]
      condition {
        test     = "StringNotEquals"
        variable = "bedrock:GuardrailIdentifier"
        values   = [local.guardrail_ref]
      }
    }
  }

  statement {
    sid       = "ApplyTenantGuardrail"
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [aws_bedrock_guardrail.this.guardrail_arn]
  }

  statement {
    sid       = "ContactState"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.contact_state.arn]
  }

  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [local.f.kms_data_arn]
  }

  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["Serve7/GenAIGateway"]
    }
  }
}

module "gateway" {
  source                   = "../../modules/lambda-function"
  name                     = "${local.prefix}-genai-gateway"
  source_dir               = "${path.module}/../../lambdas/genai_gateway"
  timeout                  = 7
  memory_size              = 512
  role_path                = var.workload_role_path
  permissions_boundary_arn = local.boundary_arn
  logs_kms_key_arn         = local.f.kms_logs_arn
  policy_json              = data.aws_iam_policy_document.gateway.json
  environment = {
    MODEL_ID          = local.invoke_target
    GUARDRAIL_ID      = aws_bedrock_guardrail.this.guardrail_arn
    GUARDRAIL_VERSION = aws_bedrock_guardrail_version.this.version
    STATE_TABLE       = aws_dynamodb_table.contact_state.name
    STATE_TTL_DAYS    = tostring(var.contact_state_ttl_days)
    TENANT            = var.tenant
    METRIC_NAMESPACE  = "Serve7/GenAIGateway"
  }
  tags = { "serve7:component" = "genai-gateway", "serve7:genai" = "bedrock" }
}

# Connect -> Lambda with confused-deputy protection, then associate with the instance.
resource "aws_lambda_permission" "connect_gateway" {
  statement_id   = "AllowConnectInvoke"
  action         = "lambda:InvokeFunction"
  function_name  = module.gateway.name
  principal      = "connect.amazonaws.com"
  source_arn     = local.c.instance_arn
  source_account = local.acct
}

resource "aws_connect_lambda_function_association" "gateway" {
  instance_id  = local.c.instance_id
  function_arn = module.gateway.arn
  depends_on   = [aws_lambda_permission.connect_gateway]
}

# ============================================================ Bedrock invocation logging (PII store)
module "kms_bedrock_logs" {
  count              = var.enable_invocation_logging ? 1 : 0
  source             = "../../modules/kms-key"
  name               = "${local.prefix}-bedrock-logs"
  description        = "Bedrock model invocation logs"
  service_principals = ["bedrock.amazonaws.com"]
}

module "bucket_bedrock_logs" {
  count       = var.enable_invocation_logging ? 1 : 0
  source      = "../../modules/s3-secure-bucket"
  name        = "${local.prefix}-bedrock-logs-${local.acct}"
  kms_key_arn = module.kms_bedrock_logs[0].arn
  expire_days = 90
  tags        = { "serve7:component" = "bedrock-invocation-logs", "serve7:data-classification" = "restricted" }
  extra_policy_statements = [
    {
      Sid       = "BedrockLogDelivery"
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "s3:PutObject"
      Resource  = "arn:${local.part}:s3:::${local.prefix}-bedrock-logs-${local.acct}/AWSLogs/${local.acct}/BedrockModelInvocationLogs/*"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.acct }
        ArnLike      = { "aws:SourceArn" = "arn:${local.part}:bedrock:${var.region}:${local.acct}:*" }
      }
    }
  ]
}

resource "aws_bedrock_model_invocation_logging_configuration" "this" {
  count = var.enable_invocation_logging ? 1 : 0
  logging_config {
    text_data_delivery_enabled      = true
    image_data_delivery_enabled     = false
    embedding_data_delivery_enabled = false
    video_data_delivery_enabled     = false
    s3_config {
      bucket_name = module.bucket_bedrock_logs[0].name
    }
  }
}

# ============================================================ outputs
output "gateway_lambda_arn" {
  value = module.gateway.arn
}

output "gateway_lambda_name" {
  value = module.gateway.name
}

output "guardrail_id" {
  value = aws_bedrock_guardrail.this.guardrail_id
}

output "guardrail_version" {
  value = aws_bedrock_guardrail_version.this.version
}

output "contact_state_table" {
  value = aws_dynamodb_table.contact_state.name
}

output "model_invoke_target" {
  value = local.invoke_target
}
