tenant              = "sandbox"
environment         = "dev"
region              = "ca-central-1"
cost_center         = "CC-SERVE7-RND"
owner               = "cx-platform@serve7.io"
data_classification = "internal"

# 10-foundation
recording_retention_days = 90

# 20-connect-core
instance_alias      = "serve7-sandbox-dev"
kinesis_stream_mode = "PROVISIONED" # sandbox: fixed 1 shard instead of on-demand

# 40-genai: bedrock_model_id / bedrock_resource_arns are written by scripts/preflight.py
# into stacks/40-genai/models.auto.tfvars.json at deploy time.
enforce_guardrail_iam     = true
enable_invocation_logging = true
enable_agentic_tools      = true
demo_crm_file             = "../../tenants/sandbox/demo-crm.json"

# 30-routing
routing_file       = "../../tenants/sandbox/routing.yaml"
claim_phone_number = false # new accounts start with a 0 phone-number quota; request an increase then flip
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
