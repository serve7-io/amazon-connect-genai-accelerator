# Data tables: schema as code + the Data Table Manager backend Lambda.
#   tenants/<tenant>/data-tables.yaml  -> one CloudFormation stack per table (AWS::Connect::DataTable + attributes)
#   rows in the same file              -> seeded by scripts/sync-data-tables.py through the Lambda (create-only by
#                                         default, so values that business users edit in Connect are never overwritten)
#   lambdas/data_table_manager         -> List/Describe/Create/Update tables, read flows, IdentifyTableRequirements;
#                                         the backend for the Data Table Manager UI (docs/data-table-manager.md)

variable "enable_data_table_manager" {
  description = "Deploy the Data Table Manager Lambda (also needed to seed rows from data-tables.yaml)."
  type        = bool
  default     = true
}

data "terraform_remote_state" "foundation" {
  backend = "s3"
  config  = { bucket = var.state_bucket, key = "10-foundation/terraform.tfstate", region = var.region }
}

locals {
  data_tables_path = "${path.module}/../../tenants/${var.tenant}/data-tables.yaml"
  data_tables      = try(yamldecode(file(local.data_tables_path)).tables, {})
  dt_instance_arn  = local.c.instance_arn
  dt_instance_id   = local.c.instance_id
}

resource "aws_cloudformation_stack" "data_table" {
  for_each   = local.data_tables
  name       = "${local.prefix}-dt-${lower(replace(each.key, "_", "-"))}"
  on_failure = "ROLLBACK"
  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Description              = "Serve7 accelerator - Connect data table ${each.key} (tenants/${var.tenant}/data-tables.yaml)"
    Resources = merge(
      {
        # Data in the table is business-owned: keep it if the stack or the resource is ever removed or replaced.
        Table = {
          Type                = "AWS::Connect::DataTable"
          DeletionPolicy      = "Retain"
          UpdateReplacePolicy = "Retain"
          Properties = merge(
            {
              InstanceArn    = local.dt_instance_arn
              Name           = each.key
              Status         = "PUBLISHED"
              TimeZone       = try(each.value.time_zone, "America/Toronto")
              ValueLockLevel = try(each.value.value_lock_level, "PRIMARY_VALUE")
            },
            try(each.value.description, "") != "" ? { Description = each.value.description } : {},
          )
        }
      },
      {
        # Logical id from the attribute name, so reordering attributes in YAML never replaces one.
        for a in each.value.attributes : "Attr${replace(a.name, "/[^A-Za-z0-9]/", "")}" => {
          Type                = "AWS::Connect::DataTableAttribute"
          DeletionPolicy      = "Retain"
          UpdateReplacePolicy = "Retain"
          Properties = merge(
            {
              InstanceArn  = local.dt_instance_arn
              DataTableArn = { "Fn::GetAtt" = ["Table", "Arn"] }
              Name         = a.name
              ValueType    = upper(try(a.type, "TEXT"))
              Primary      = try(a.primary, false)
            },
            try(a.description, "") != "" ? { Description = a.description } : {},
            try(a.validation, null) != null ? { Validation = a.validation } : {},
          )
        }
      },
    )
    Outputs = {
      DataTableArn = { Value = { "Fn::GetAtt" = ["Table", "Arn"] } }
    }
  })

  tags = { "serve7:component" = "data-table" }

  lifecycle {
    ignore_changes = [tags, tags_all]
  }
}

data "aws_iam_policy_document" "data_table_manager" {
  statement {
    sid = "DataTables"
    actions = [
      "connect:ListDataTables", "connect:DescribeDataTable", "connect:ListDataTableAttributes",
      "connect:ListDataTableValues", "connect:CreateDataTable", "connect:CreateDataTableAttribute",
      "connect:BatchCreateDataTableValue", "connect:BatchUpdateDataTableValue",
    ]
    resources = [local.dt_instance_arn, "${local.dt_instance_arn}/*"]
  }
  statement {
    sid       = "ReadFlows"
    actions   = ["connect:ListContactFlows", "connect:DescribeContactFlow"]
    resources = [local.dt_instance_arn, "${local.dt_instance_arn}/*"]
  }
}

module "data_table_manager" {
  count  = var.enable_data_table_manager ? 1 : 0
  source = "../../modules/lambda-function"

  name                     = "${local.prefix}-data-table-manager"
  source_dir               = "${path.module}/../../lambdas/data_table_manager"
  handler                  = "handler.lambda_handler"
  runtime                  = "python3.13"
  timeout                  = 60
  memory_size              = 256
  policy_json              = data.aws_iam_policy_document.data_table_manager.json
  role_path                = var.workload_role_path
  permissions_boundary_arn = local.boundary_arn
  logs_kms_key_arn         = data.terraform_remote_state.foundation.outputs.kms_logs_arn
  environment = {
    INSTANCE_ID       = local.dt_instance_id
    DEFAULT_TIME_ZONE = try(local.routing.hours.business.timezone, "America/Toronto")
  }
  tags = { "serve7:component" = "data-table-manager" }
}

output "data_table_manager_function" {
  value = try(module.data_table_manager[0].name, "")
}

output "data_tables" {
  description = "Data tables managed from data-tables.yaml: name -> ARN."
  value       = { for k, s in aws_cloudformation_stack.data_table : k => s.outputs["DataTableArn"] }
}
