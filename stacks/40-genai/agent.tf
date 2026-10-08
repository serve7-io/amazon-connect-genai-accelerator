# Agentic self-service, part 2: the AI agent that uses the tools from agentic.tf.
#   MCP integration (Connect -> AgentCore gateway) + AI security profile (3 CRM tools, Wisdom.View)
#   + ORCHESTRATION AI agent (knowledge base, CRM tools, escalate/complete) + published version.
# scripts/configure-ai-agent.py (run by deploy.sh) then links the profile to the agent and makes the
# newest version the assistant's self-service default; neither step has a CloudFormation resource.

variable "enable_ai_agent" {
  description = "Create the self-service AI agent on top of the agentic tools (needs enable_agentic_tools and enable_self_service)."
  type        = bool
  default     = true
}

variable "ai_agent_orchestration_prompt_id" {
  description = "AWS-managed prompt SelfServiceOrchestrationChat (id:version) the agent is built on."
  type        = string
  default     = "156bf3e3-4f10-4274-bc51-14739238eb23:3"
}

variable "ai_agent_profile_permissions" {
  description = "Connect permissions of the AI agent security profile. Wisdom.View lets the agent use knowledge retrieval."
  type        = list(string)
  default     = ["Wisdom.View"]
}

locals {
  ai_agent = local.agentic && var.enable_ai_agent && var.enable_self_service
}

resource "aws_cloudformation_stack" "ai_agent" {
  count         = local.ai_agent ? 1 : 0
  name          = "${local.prefix}-ai-agent"
  template_body = file("${path.module}/../../modules/ai-selfservice-agent/template.yaml")
  on_failure    = "ROLLBACK"

  parameters = {
    Prefix                = local.prefix
    ConnectInstanceArn    = local.c.instance_arn
    AssistantId           = aws_cloudformation_stack.self_service[0].outputs["AssistantId"]
    GatewayId             = aws_cloudformation_stack.agent_tools_gateway[0].outputs["GatewayId"]
    GatewayUrl            = aws_cloudformation_stack.agent_tools_gateway[0].outputs["GatewayUrl"]
    OrchestrationPromptId = var.ai_agent_orchestration_prompt_id
    ProfilePermissions    = join(",", var.ai_agent_profile_permissions)
    Locale                = var.self_service_locale
  }

  tags = { "serve7:component" = "ai-agent", "serve7:genai" = "agentic" }

  lifecycle {
    ignore_changes = [tags, tags_all]
  }
}

output "ai_agent_id" {
  value = try(aws_cloudformation_stack.ai_agent[0].outputs["AgentId"], "")
}

output "ai_agent_arn" {
  value = try(aws_cloudformation_stack.ai_agent[0].outputs["AgentArn"], "")
}

output "ai_agent_security_profile_arn" {
  value = try(aws_cloudformation_stack.ai_agent[0].outputs["SecurityProfileArn"], "")
}
