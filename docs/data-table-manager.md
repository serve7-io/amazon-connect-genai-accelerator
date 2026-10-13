# Data Table Manager

Business-editable reference data for Amazon Connect flows (prompts per language, emergency closures, routing
switches), managed as part of the accelerator. Based on the team's "Amazon Connect Lambda Functions" spec and
Dan's UI mockup; the spec defines intent, and this implementation follows the real Connect data table APIs.

## What is deployed

| Piece | Where | What it does |
|---|---|---|
| Table schema as code | `tenants/<t>/data-tables.yaml` → `stacks/30-routing/datatables.tf` | One CloudFormation stack per table (`AWS::Connect::DataTable` + `AWS::Connect::DataTableAttribute`), `DeletionPolicy: Retain` |
| Seed rows | same YAML → `scripts/sync-data-tables.py` (run by `deploy.sh` after layer 30) | **Create-only**: never overwrites values that business users changed in Connect. `--overwrite` makes the YAML win |
| Backend Lambda | `lambdas/data_table_manager`, function `s7-<tenant>-<env>-data-table-manager` | The operations below; the backend for the UI. Scoped by IAM to the tenant's own instance |
| UI | Not built yet | Next step: Dan's 4 screens (list, view, create, edit) on top of this Lambda |

## Operations (`{"operation": "<Name>", ...}`)

| Spec function | Operation | Notes / differences from the spec |
|---|---|---|
| 1 ListDataTables | `ListDataTables` | `InstanceId` optional; the function only serves its own instance |
| 2 DescribeDataTable | `DescribeDataTable` | Connect's DescribeDataTable returns no attributes; we add `ListDataTableAttributes`. Accepts name, id or ARN |
| (UI view) | `GetDataTableRows` | Pivots the value list into grid rows (Screen 2) |
| 3 CreateDataTable | `CreateDataTable` | Connect creates the table and each attribute in separate calls; primary attributes are created first. Defaults: `TimeZone` from the tenant's business hours, `ValueLockLevel` `PRIMARY_VALUE`, `Status` `PUBLISHED` |
| 4 UpdateDataTable | `UpdateDataTable` | Connect has **no upsert**: we read current values once, create missing ones (`BatchCreateDataTableValue`) and update changed ones with their **lock versions** (`BatchUpdateDataTableValue`). Returns `SuccessCount`, `Created`, `Updated`, `Unchanged`, `Failures`. `Mode: create_only` for seeding |
| 5 ListContactFlows | `ListContactFlows` | Optional `ContactFlowType` filter |
| 6 DescribeContactFlow | `DescribeContactFlow` | Name or ARN/id; `Content` returned as parsed JSON |
| 7 IdentifyTableRequirements | `IdentifyTableRequirements` | See below |

### IdentifyTableRequirements: one important correction

In a real flow, values are read with a **Data Table block** (Evaluate) and referenced as
`$.DataTables.<QueryName>.<Attribute>`. `QueryName` is the name of the query in that block, **not the table name**
(the block holds the table ARN). The function therefore:

- groups references per query name and reports the table ARN when the block that uses it references one
  (`ExistingTableArns`);
- supports the spec's placeholder convention for flows designed before the table exists:
  `$.DataTables.<Table>.<Key...>.<Attribute>`. Middle segments become primary-key columns `Key1..KeyN`, with the
  example values in the description (e.g. `$.DataTables.Sales.English.Welcome` gives `Key1` (English) + `Welcome`);
- returns `Tables[]` always, plus the spec's flat `TableName` / `Attributes` / `References` when exactly one table is
  found, so the output can go straight into `CreateDataTable`.

Static analysis only; no AWS calls. Verify the Data Table block's exact JSON on the first exported flow (the action
type is not in the published flow-language reference yet).

## data-tables.yaml

See `templates/tenant/data-tables.yaml.example`. Rules (checked by `scripts/check-tenants.py` in CI):

- table names: letters, digits, `_`, `-`; up to 100 tables per instance;
- attribute types: `TEXT`, `NUMBER`, `BOOLEAN`, `TEXT_LIST`, `NUMBER_LIST`; names unique;
- `validation` uses CloudFormation names (`MinLength`, `MaxLength`, `Minimum`, `Maximum`, `Enum: {Strict, Values}`);
- every row has all primary attributes, no unknown attributes, and no duplicate keys.

Changing an attribute's `primary` flag or removing an attribute is a schema change on a live table. Do it in a PR,
and expect flows that use the table to need re-testing.

## Permissions

- The Lambda role allows only data-table and flow-read actions on the tenant's instance ARN.
- `bootstrap.yaml` adds the four data-table write actions to the workload permissions boundary. **Re-run the
  bootstrap** (any `deploy.sh` run does) before the first deploy with data tables.

## Not done yet / to verify on first deploy

- The UI (next step): React or plain HTML hosted in the client account behind Cognito, calling the Lambda through an
  authenticated API.
- The Python 3.13 Lambda runtime's bundled boto3 must include the data-table APIs (GA Dec 2025 / Sep 2026). If a call
  fails with "unknown operation", vendor a newer boto3 into the function.
- Data Table block flow JSON (see above).
