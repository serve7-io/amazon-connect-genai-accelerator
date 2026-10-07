# Agentic self-service, part 1: the tools the Q in Connect orchestration agent can call.
#   demo CRM table (sandbox only, fictional data) -> tools Lambda -> AgentCore Gateway (MCP) -> AI agent
# The AI agent + its MCP tool configuration on the Q in Connect assistant is part 2 (agent.tf).

variable "enable_agentic_tools" {
  type    = bool
  default = false
}

variable "demo_crm_file" {
  description = "JSON seed file with fictional customers (sandbox). Empty = table created without seed data."
  type        = string
  default     = ""
}

variable "agent_gateway_authorizer" {
  description = "CUSTOM_JWT = Amazon Connect AI agents call the gateway with instance-issued JWTs."
  type        = string
  default     = "CUSTOM_JWT"
}

locals {
  agentic   = var.enable_agentic_tools
  demo_crm  = local.agentic && var.demo_crm_file != "" ? jsondecode(file(var.demo_crm_file)).customers : []
  crm_items = merge(
    { for c in local.demo_crm : "${c.customer_id}/PROFILE" => {
      pk = "CUST#${c.customer_id}", sk = "PROFILE",
      attrs = { first_name = c.first_name, last_name = c.last_name, tier = c.tier, phone = c.phone, email = c.email }
    } },
    { for c in local.demo_crm : "${c.customer_id}/PHONE" => {
      pk = "PHONE#${c.phone}", sk = "ALIAS", attrs = { customer_id = c.customer_id }
    } },
    { for c in local.demo_crm : "${c.customer_id}/EMAIL" => {
      pk = "EMAIL#${lower(c.email)}", sk = "ALIAS", attrs = { customer_id = c.customer_id }
    } },
    merge([for c in local.demo_crm : { for o in c.orders : "${c.customer_id}/ORDER/${o.order_id}" => {
      pk = "CUST#${c.customer_id}", sk = "ORDER#${o.order_id}", attrs = { item = o.item, status = o.status, eta = o.eta }
    } }]...)
  )
}

resource "aws_dynamodb_table" "crm" {
  count        = local.agentic ? 1 : 0
  name         = "${local.prefix}-demo-crm"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = local.f.kms_data_arn
  }

  tags = { "serve7:component" = "agent-tools-crm" }
}

# Seed rows are created once; tickets the agent creates later are not managed by Terraform.
resource "aws_dynamodb_table_item" "crm_seed" {
  for_each   = local.crm_items
  table_name = aws_dynamodb_table.crm[0].name
  hash_key   = "pk"
  range_key  = "sk"
  item = jsonencode(merge(
    { pk = { S = each.value.pk }, sk = { S = each.value.sk } },
    { for k, v in each.value.attrs : k => { S = v } }
  ))
}

data "aws_iam_policy_document" "agent_tools" {
  count = local.agentic ? 1 : 0
  statement {
    sid       = "Crm"
    actions   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.crm[0].arn]
  }
  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [local.f.kms_data_arn]
  }
}

module "agent_tools" {
  count                    = local.agentic ? 1 : 0
  source                   = "../../modules/lambda-function"
  name                     = "${local.prefix}-agent-tools"
  source_dir               = "${path.module}/../../lambdas/agent_tools"
  timeout                  = 5
  memory_size              = 256
  role_path                = var.workload_role_path
  permissions_boundary_arn = local.boundary_arn
  logs_kms_key_arn         = local.f.kms_logs_arn
  policy_json              = data.aws_iam_policy_document.agent_tools[0].json
  environment              = { CRM_TABLE = aws_dynamodb_table.crm[0].name }
  tags                     = { "serve7:component" = "agent-tools", "serve7:genai" = "agentic" }
}

resource "aws_lambda_permission" "gateway_tools" {
  count          = local.agentic ? 1 : 0
  statement_id   = "AllowAgentCoreGateway"
  action         = "lambda:InvokeFunction"
  function_name  = module.agent_tools[0].name
  principal      = "bedrock-agentcore.amazonaws.com"
  source_account = local.acct
}

resource "aws_cloudformation_stack" "agent_tools_gateway" {
  count         = local.agentic ? 1 : 0
  name          = "${local.prefix}-agent-tools-gateway"
  template_body = file("${path.module}/../../modules/agent-tools-gateway/template.yaml")
  capabilities  = ["CAPABILITY_IAM"]
  on_failure    = "ROLLBACK"

  parameters = {
    Prefix                 = local.prefix
    ToolsLambdaArn         = module.agent_tools[0].arn
    PermissionsBoundaryArn = local.boundary_arn
    RolePath               = var.workload_role_path
    AuthorizerType         = var.agent_gateway_authorizer
    # Connect instance OIDC issuer: only tokens signed by this instance are accepted.
    JwtDiscoveryUrl = "${local.c.access_url}/.well-known/openid-configuration"
  }

  tags = { "serve7:component" = "agent-tools-gateway", "serve7:genai" = "agentic" }

  # Same reason as the QiC stack: a changing release tag must not trigger in-place updates.
  lifecycle {
    ignore_changes = [tags, tags_all]
  }

  depends_on = [aws_lambda_permission.gateway_tools]
}

output "agent_tools_lambda_name" {
  value = try(module.agent_tools[0].name, "")
}

output "agent_tools_gateway_arn" {
  value = try(aws_cloudformation_stack.agent_tools_gateway[0].outputs["GatewayArn"], "")
}

output "agent_tools_gateway_url" {
  value = try(aws_cloudformation_stack.agent_tools_gateway[0].outputs["GatewayUrl"], "")
}

output "demo_crm_table" {
  value = try(aws_dynamodb_table.crm[0].name, "")
}
