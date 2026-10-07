# Layer 10 - foundation: encryption keys and data buckets shared by later layers.

locals {
  layer = "10-foundation"
  acct  = data.aws_caller_identity.current.account_id
}

module "kms_connect" {
  source             = "../../modules/kms-key"
  name               = "${local.prefix}-connect"
  description        = "Connect recordings, transcripts, chat, Kinesis streams"
  service_principals = ["connect.amazonaws.com"]
}

module "kms_data" {
  source             = "../../modules/kms-key"
  name               = "${local.prefix}-data"
  description        = "DynamoDB state tables, knowledge base, Lambda environment"
  service_principals = ["wisdom.amazonaws.com", "app-integrations.amazonaws.com", "connect.amazonaws.com"]
}

module "kms_logs" {
  source       = "../../modules/kms-key"
  name         = "${local.prefix}-logs"
  description  = "CloudWatch Logs and SNS for this tenant"
  logs_service = true
  service_principals = [
    "cloudwatch.amazonaws.com", # alarm -> encrypted SNS
  ]
}

module "bucket_recordings" {
  source      = "../../modules/s3-secure-bucket"
  name        = "${local.prefix}-recordings-${local.acct}"
  kms_key_arn = module.kms_connect.arn
  expire_days = var.recording_retention_days
  tags        = { "serve7:component" = "recordings", "serve7:data-classification" = "restricted" }
}

module "bucket_knowledge" {
  source      = "../../modules/s3-secure-bucket"
  name        = "${local.prefix}-knowledge-${local.acct}"
  kms_key_arn = module.kms_data.arn
  tags        = { "serve7:component" = "knowledge-base" }
}

module "bucket_artifacts" {
  source        = "../../modules/s3-secure-bucket"
  name          = "${local.prefix}-artifacts-${local.acct}"
  kms_key_arn   = module.kms_data.arn
  force_destroy = var.environment != "prod"
  tags          = { "serve7:component" = "artifacts", "serve7:data-classification" = "internal" }
}

variable "recording_retention_days" {
  description = "Recordings and transcripts lifetime; set per client contract."
  type        = number
  default     = 365
}

output "kms_connect_arn" {
  value = module.kms_connect.arn
}

output "kms_data_arn" {
  value = module.kms_data.arn
}

output "kms_logs_arn" {
  value = module.kms_logs.arn
}

output "recordings_bucket" {
  value = module.bucket_recordings.name
}

output "recordings_bucket_arn" {
  value = module.bucket_recordings.arn
}

output "knowledge_bucket" {
  value = module.bucket_knowledge.name
}

output "knowledge_bucket_arn" {
  value = module.bucket_knowledge.arn
}

output "artifacts_bucket" {
  value = module.bucket_artifacts.name
}
