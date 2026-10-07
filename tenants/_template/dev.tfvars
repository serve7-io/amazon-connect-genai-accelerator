tenant              = "__TENANT__"
environment         = "dev"
region              = "ca-central-1"
cost_center         = "CC-SERVE7-RND"
owner               = "cx-platform@serve7.io"
data_classification = "internal"

# 10-foundation
recording_retention_days = 90

# 20-connect-core
instance_alias = "serve7-__TENANT__-dev"

# 40-genai: bedrock_model_id / bedrock_resource_arns are written by scripts/preflight.py
# into stacks/40-genai/models.auto.tfvars.json at deploy time.
enforce_guardrail_iam     = true
enable_invocation_logging = true

# 30-routing
routing_file       = "../../tenants/__TENANT__/routing.yaml"
claim_phone_number = true
phone_country_code = "CA"
admin_users = [
  {
    username   = "serve7.admin"
    first_name = "Serve7"
    last_name  = "Admin"
    email      = "milos.jovanic@serve7.io"
  }
]

# 50-observability
alarm_emails = ["milos.jovanic@serve7.io"]
