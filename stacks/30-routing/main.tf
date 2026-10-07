# Layer 30 - routing: hours, queues, routing profiles, flows, phone number, admin user.
# Everything is driven by tenants/<tenant>/routing.yaml.

locals {
  layer = "30-routing"
  # SAML instances: users authenticate at the IdP, so no Connect password / secret is created.
  local_passwords = try(data.terraform_remote_state.core.outputs.identity_management_type, "CONNECT_MANAGED") == "CONNECT_MANAGED"
  password_users  = local.local_passwords ? { for u in var.admin_users : u.username => u } : {}
  c               = data.terraform_remote_state.core.outputs
  g               = data.terraform_remote_state.genai.outputs
  routing         = yamldecode(file(var.routing_file))

  day_names = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
}

data "terraform_remote_state" "core" {
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "20-connect-core/terraform.tfstate", region = var.region }
}

data "terraform_remote_state" "genai" {
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "40-genai/terraform.tfstate", region = var.region }
}

variable "routing_file" {
  description = "Path to tenants/<tenant>/routing.yaml"
  type        = string
}

variable "flows_dir" {
  type    = string
  default = "../../flows"
}

variable "claim_phone_number" {
  type    = bool
  default = false
}

variable "phone_country_code" {
  type    = string
  default = "CA"
}

variable "admin_users" {
  description = "Connect-managed admin users (sandbox only; prod uses SAML)."
  type = list(object({
    username   = string
    first_name = string
    last_name  = string
    email      = string
  }))
  default = []
}

# ------------------------------------------------------------ hours of operation
resource "aws_connect_hours_of_operation" "this" {
  for_each    = local.routing.hours
  instance_id = local.c.instance_id
  name        = "${local.prefix}-${each.key}"
  description = "Managed by Terraform"
  time_zone   = each.value.timezone

  dynamic "config" {
    for_each = { for d in local.day_names : d => each.value.days[d] if try(each.value.days[d], null) != null }
    content {
      day = config.key
      start_time {
        hours   = tonumber(split(":", config.value[0])[0])
        minutes = tonumber(split(":", config.value[0])[1])
      }
      end_time {
        hours   = tonumber(split(":", config.value[1])[0])
        minutes = tonumber(split(":", config.value[1])[1])
      }
    }
  }
}

# ------------------------------------------------------------ queues
resource "aws_connect_queue" "this" {
  for_each              = local.routing.queues
  instance_id           = local.c.instance_id
  name                  = "${local.prefix}-${each.key}"
  description           = try(each.value.description, "Managed by Terraform")
  hours_of_operation_id = aws_connect_hours_of_operation.this[each.value.hours].hours_of_operation_id
  max_contacts          = try(each.value.max_contacts, null)
  tags                  = { "serve7:component" = "queue" }
}

# ------------------------------------------------------------ routing profiles
resource "aws_connect_routing_profile" "this" {
  for_each                  = local.routing.routing_profiles
  instance_id               = local.c.instance_id
  name                      = "${local.prefix}-${each.key}"
  description               = "Managed by Terraform"
  default_outbound_queue_id = aws_connect_queue.this[each.value.default_outbound_queue].queue_id

  dynamic "media_concurrencies" {
    for_each = each.value.media
    content {
      channel     = media_concurrencies.key
      concurrency = media_concurrencies.value
    }
  }

  dynamic "queue_configs" {
    for_each = flatten([
      for q in each.value.queues : [
        for ch in keys(each.value.media) : { queue = q.name, priority = q.priority, delay = q.delay, channel = ch }
      ]
    ])
    content {
      channel  = queue_configs.value.channel
      delay    = queue_configs.value.delay
      priority = queue_configs.value.priority
      queue_id = aws_connect_queue.this[queue_configs.value.queue].queue_id
    }
  }

  tags = { "serve7:component" = "routing-profile" }
}

# ------------------------------------------------------------ contact flows
resource "aws_connect_contact_flow" "inbound_main" {
  instance_id = local.c.instance_id
  name        = "${local.prefix}-inbound-main"
  description = "AI-assisted inbound flow (release ${var.release})"
  type        = "CONTACT_FLOW"
  content = templatefile("${var.flows_dir}/inbound-main.json.tftpl", {
    gateway_lambda_arn    = local.g.gateway_lambda_arn
    hours_arn             = aws_connect_hours_of_operation.this["business"].arn
    general_queue_arn     = aws_connect_queue.this["general"].arn
    billing_queue_arn     = aws_connect_queue.this["billing"].arn
    recorded_participants = jsonencode(local.routing.recording.participants)
    analytics_enabled     = local.routing.recording.analytics ? "True" : "False"
    analytics_language    = local.routing.recording.language
    disclosure_text       = local.routing.messages.ai_disclosure
    closed_text           = local.routing.messages.closed
    failure_text          = local.routing.messages.failure
    self_service          = try(local.g.lex_bot_alias_arn, "") != ""
    qic_assistant_arn     = try(local.g.qic_assistant_arn, "")
    lex_bot_alias_arn     = try(local.g.lex_bot_alias_arn, "")
    self_service_prompt   = try(local.routing.messages.self_service_prompt, "How can I help you today?")
    goodbye_text          = try(local.routing.messages.goodbye, "Thanks for contacting us. Goodbye.")
  })
  tags = { "serve7:component" = "contact-flow" }
}

# ------------------------------------------------------------ phone number (optional)
resource "aws_connect_phone_number" "main" {
  count        = var.claim_phone_number ? 1 : 0
  target_arn   = local.c.instance_arn
  country_code = var.phone_country_code
  type         = "DID"
  description  = "${local.prefix} main inbound"
  tags         = { "serve7:component" = "phone-number" }
}

resource "aws_connect_phone_number_contact_flow_association" "main" {
  count           = var.claim_phone_number ? 1 : 0
  phone_number_id = aws_connect_phone_number.main[0].id
  instance_id     = local.c.instance_id
  contact_flow_id = aws_connect_contact_flow.inbound_main.contact_flow_id
}

# ------------------------------------------------------------ admin users (sandbox)
data "aws_connect_security_profile" "admin" {
  instance_id = local.c.instance_id
  name        = "Admin"
}

data "aws_connect_security_profile" "agent" {
  instance_id = local.c.instance_id
  name        = "Agent"
}

resource "random_password" "admin" {
  for_each         = local.password_users
  length           = 20
  special          = true
  override_special = "!#%*-_+="
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
  min_special      = 1
}

resource "aws_secretsmanager_secret" "admin" {
  for_each                = local.password_users
  name                    = "${local.prefix}/connect-user/${each.key}"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "admin" {
  for_each      = aws_secretsmanager_secret.admin
  secret_id     = each.value.id
  secret_string = jsonencode({ username = each.key, password = random_password.admin[each.key].result, url = local.c.access_url })
}

resource "aws_connect_user" "admin" {
  for_each             = { for u in var.admin_users : u.username => u }
  instance_id          = local.c.instance_id
  name                 = each.key
  password             = local.local_passwords ? random_password.admin[each.key].result : null
  routing_profile_id   = aws_connect_routing_profile.this["tier1"].routing_profile_id
  security_profile_ids = [data.aws_connect_security_profile.admin.security_profile_id, data.aws_connect_security_profile.agent.security_profile_id]

  identity_info {
    first_name = each.value.first_name
    last_name  = each.value.last_name
    email      = each.value.email
  }

  phone_config {
    phone_type  = "SOFT_PHONE"
    auto_accept = false
  }

  # The initial password lives in Secrets Manager; users reset it themselves after first login.
  lifecycle {
    ignore_changes = [password]
  }
}

output "inbound_flow_id" {
  value = aws_connect_contact_flow.inbound_main.contact_flow_id
}

output "queue_ids" {
  value = { for k, q in aws_connect_queue.this : k => q.queue_id }
}

output "phone_number" {
  value = try(aws_connect_phone_number.main[0].phone_number, null)
}

output "admin_user_secrets" {
  value = { for k, s in aws_secretsmanager_secret.admin : k => s.name }
}
