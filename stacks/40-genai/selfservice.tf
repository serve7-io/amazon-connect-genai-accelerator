# Amazon Q in Connect self-service (knowledge-base answers through Lex), deployed as a CloudFormation
# stack so the Wisdom/Lex/Connect-integration resources are validated against AWS's own schemas (cfn-lint).

variable "enable_self_service" {
  type    = bool
  default = true
}

variable "self_service_locale" {
  type    = string
  default = "en_US"
}

resource "aws_cloudformation_stack" "self_service" {
  count         = var.enable_self_service ? 1 : 0
  name          = "${local.prefix}-qic-selfservice"
  template_body = file("${path.module}/../../modules/qic-selfservice/template.yaml")
  capabilities  = ["CAPABILITY_IAM"]
  on_failure    = "ROLLBACK"

  parameters = {
    Prefix                 = local.prefix
    ConnectInstanceArn     = local.c.instance_arn
    KmsKeyArn              = local.f.kms_data_arn
    PermissionsBoundaryArn = local.boundary_arn
    RolePath               = var.workload_role_path
    LocaleId               = var.self_service_locale
  }

  tags = { "serve7:component" = "qic-selfservice", "serve7:genai" = "qic" }

  timeouts {
    create = "30m"
    update = "30m"
  }

  # Stack tags propagate to every resource inside; Wisdom associations and Connect integration
  # associations cannot be updated in place, so a changing serve7:release tag breaks the stack.
  lifecycle {
    ignore_changes = [tags, tags_all]
  }
}

output "qic_assistant_arn" {
  value = try(aws_cloudformation_stack.self_service[0].outputs["AssistantArn"], "")
}

output "qic_assistant_id" {
  value = try(aws_cloudformation_stack.self_service[0].outputs["AssistantId"], "")
}

output "qic_knowledge_base_id" {
  value = try(aws_cloudformation_stack.self_service[0].outputs["KnowledgeBaseId"], "")
}

output "lex_bot_alias_arn" {
  value = try(aws_cloudformation_stack.self_service[0].outputs["BotAliasArn"], "")
}
