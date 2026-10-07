# Shared by every stack (symlinked as common.tf). Do not put layer-specific resources here.

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Partial config: bucket/key/region are injected by the pipeline (scripts/tf.sh).
  backend "s3" {
    encrypt      = true
    use_lockfile = true
  }
}

variable "tenant" {
  description = "Tenant slug, matches tenants/<dir>."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9-]{2,20}$", var.tenant))
    error_message = "tenant must be a lowercase slug."
  }
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "test", "prod"], var.environment)
    error_message = "environment must be dev, test or prod."
  }
}

variable "region" {
  type = string
}

variable "state_bucket" {
  description = "Terraform state bucket created by bootstrap (serve7-tfstate-<account>-<region>)."
  type        = string
}

variable "release" {
  description = "Git tag or SHA of this deploy; stamped on every resource."
  type        = string
  default     = "local"
}

variable "owner" {
  type    = string
  default = "cx-platform@serve7.io"
}

variable "cost_center" {
  type = string
}

variable "data_classification" {
  type    = string
  default = "confidential"
}

variable "workload_role_path" {
  description = "IAM path the deploy role may create and pass."
  type        = string
  default     = "/serve7/workload/"
}

locals {
  prefix = "s7-${var.tenant}-${var.environment}"

  base_tags = {
    "serve7:tenant"              = var.tenant
    "serve7:environment"         = var.environment
    "serve7:layer"               = local.layer
    "serve7:owner"               = var.owner
    "serve7:cost-center"         = var.cost_center
    "serve7:data-classification" = var.data_classification
    "serve7:managed-by"          = "terraform"
    "serve7:release"             = var.release
  }

  boundary_arn = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/serve7-workload-boundary"
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.base_tags
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}
