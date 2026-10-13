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

# Per-tenant behaviour of the agent comes from tenants/<tenant>/agent.yaml (see templates/tenant/agent.yaml).
# Without that file the agent behaves as before (AWS-managed prompt, generic examples).
locals {
  ai_agent = local.agentic && var.enable_ai_agent && var.enable_self_service

  agent_yaml_path = "${path.module}/../../tenants/${var.tenant}/agent.yaml"
  agent_yaml      = try(yamldecode(file(local.agent_yaml_path)), {})
  agent_cfg = {
    company             = try(tostring(local.agent_yaml.company), "")
    assistant_name      = try(tostring(local.agent_yaml.assistant_name), "")
    persona             = trimspace(try(tostring(local.agent_yaml.persona), ""))
    customer_id_example = try(tostring(local.agent_yaml.customer_id_example), "C1001")
    ticket_categories   = try([for c in local.agent_yaml.ticket_categories : lower(tostring(c))], ["billing", "technical", "order", "account", "other"])
  }

  # A persona needs the tenant's own orchestration prompt: the AWS-managed prompt text (fetched once with
  # scripts/fetch-orchestration-prompt.py into the module) with the persona put where the marker is.
  base_prompt_path = "${path.module}/../../modules/ai-selfservice-agent/prompts/orchestration-chat.base.json"
  base_prompt      = try(jsondecode(file(local.base_prompt_path)), null)
  custom_prompt    = local.ai_agent && local.agent_cfg.persona != "" && local.base_prompt != null
  persona_block = join("\n", [
    for l in split("\n", join("\n", compact([
      local.agent_cfg.company != "" ? "You are the customer service assistant of ${local.agent_cfg.company}${local.agent_cfg.assistant_name != "" ? ", named ${local.agent_cfg.assistant_name}" : ""}." : "",
      local.agent_cfg.persona,
    ]))) : "${try(local.base_prompt.indent, "")}${l}"
  ])
  prompt_text = local.custom_prompt ? try(replace(local.base_prompt.text, local.base_prompt.marker, local.persona_block), "") : ""
}

check "agent_persona_ready" {
  assert {
    condition     = local.agent_cfg.persona == "" || local.base_prompt != null
    error_message = "tenants/${var.tenant}/agent.yaml has a persona, but modules/ai-selfservice-agent/prompts/orchestration-chat.base.json is missing (run scripts/fetch-orchestration-prompt.py). The agent keeps the AWS-managed prompt."
  }
  assert {
    condition     = !strcontains(local.agent_cfg.persona, "{{")
    error_message = "agent.yaml persona must not contain '{{' (reserved for prompt variables)."
  }
}

# The tenant's orchestration prompt + a published version (new version whenever the text changes).
resource "aws_cloudformation_stack" "ai_agent_prompt" {
  count      = local.custom_prompt ? 1 : 0
  name       = "${local.prefix}-ai-agent-prompt"
  on_failure = "ROLLBACK"
  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Description              = "Serve7 accelerator - tenant orchestration prompt for the self-service AI agent (stacks/40-genai/agent.tf)"
    Resources = {
      Prompt = {
        Type = "AWS::Wisdom::AIPrompt"
        Properties = {
          AssistantId           = aws_cloudformation_stack.self_service[0].outputs["AssistantId"]
          Name                  = "${local.prefix}-orchestration"
          Description           = "AWS SelfServiceOrchestrationChat + ${local.agent_cfg.company} persona"
          Type                  = "ORCHESTRATION"
          ApiFormat             = local.base_prompt.api_format
          ModelId               = local.base_prompt.model_id
          TemplateType          = "TEXT"
          TemplateConfiguration = { TextFullAIPromptEditTemplateConfiguration = { Text = local.prompt_text } }
        }
      }
      PromptVersion = {
        Type                = "AWS::Wisdom::AIPromptVersion"
        DeletionPolicy      = "Retain"
        UpdateReplacePolicy = "Retain"
        Properties = {
          AssistantId         = aws_cloudformation_stack.self_service[0].outputs["AssistantId"]
          AIPromptId          = { "Fn::GetAtt" = ["Prompt", "AIPromptId"] }
          ModifiedTimeSeconds = { "Fn::GetAtt" = ["Prompt", "ModifiedTimeSeconds"] }
        }
      }
    }
    Outputs = {
      PromptVersionRef = {
        Value = { "Fn::Join" = [":", [{ "Fn::GetAtt" = ["Prompt", "AIPromptId"] }, { "Fn::GetAtt" = ["PromptVersion", "VersionNumber"] }]] }
      }
    }
  })

  tags = { "serve7:component" = "ai-agent", "serve7:genai" = "agentic" }

  lifecycle {
    ignore_changes = [tags, tags_all]
  }
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
    OrchestrationPromptId = local.custom_prompt ? aws_cloudformation_stack.ai_agent_prompt[0].outputs["PromptVersionRef"] : var.ai_agent_orchestration_prompt_id
    CustomerIdExample     = local.agent_cfg.customer_id_example
    TicketCategories      = join(", ", local.agent_cfg.ticket_categories)
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

output "ai_agent_prompt" {
  description = "Orchestration prompt the agent uses (tenant prompt id:version, or the AWS-managed one)."
  value       = local.custom_prompt ? try(aws_cloudformation_stack.ai_agent_prompt[0].outputs["PromptVersionRef"], "") : var.ai_agent_orchestration_prompt_id
}

output "ai_agent_security_profile_arn" {
  value = try(aws_cloudformation_stack.ai_agent[0].outputs["SecurityProfileArn"], "")
}
