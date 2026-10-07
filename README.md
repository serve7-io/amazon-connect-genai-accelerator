# Serve7 Amazon Connect + GenAI Deployment Accelerator

Terraform + GitHub Actions framework that stands up Amazon Connect with a guarded Amazon Bedrock
layer in a fresh AWS account. Design and rationale: see the Implementation Guide (claude.ai doc).

## Layout
| Path | What |
|---|---|
| `bootstrap/bootstrap.yaml` | One-time CloudFormation per account: GitHub OIDC, `serve7-deploy`/`serve7-plan` roles, permissions boundary, state bucket, CloudTrail |
| `stacks/10-foundation` | KMS keys, recordings / knowledge / artifacts buckets |
| `stacks/20-connect-core` | Connect instance, storage config, CTR + agent-event Kinesis streams |
| `stacks/40-genai` | Bedrock guardrail, gateway Lambda, contact-state table, invocation logging |
| `stacks/30-routing` | Hours, queues, routing profile, inbound flow, phone number, admin user |
| `stacks/50-observability` | SNS, alarm baseline, dashboard |
| `flows/` | Contact flow templates (`${...}` placeholders resolved by Terraform) |
| `lambdas/genai_gateway` | Connect -> Bedrock gateway (+ prompts, unit tests) |
| `tenants/<tenant>/` | All per-client config: `tenant.yaml`, `<env>.tfvars`, `routing.yaml` |
| `scripts/` | `deploy.sh`, `destroy.sh`, `preflight.py` |
| `tests/smoke.py` | Post-deploy checks |

Apply order: **10 → 20 → 40 → 30 → 50** (flows need the gateway ARN).

## Quick test from AWS CloudShell (sandbox)
1. Sign in to the AWS console with an admin identity, switch region to **Canada (Central) ca-central-1**, open **CloudShell**.
2. Actions → Upload file → `amazon-connect-genai-accelerator.zip`, then:
   ```bash
   unzip -q amazon-connect-genai-accelerator.zip -d acc && cd acc
   ./scripts/deploy.sh sandbox dev          # asks before each layer; AUTO_APPROVE=1 to skip prompts
   python3 tests/smoke.py --tenant sandbox
   ```
3. Admin login URL and the Secrets Manager secret holding the password are printed by the smoke test.
4. Tear down: `./scripts/destroy.sh sandbox dev` (keeps the bootstrap stack and state bucket).

Options: `CLAIM_PHONE=false` (skip the phone number), `INSTANCE_ALIAS=my-alias`, `ONLY=30-routing` (one layer),
`CREATE_TRAIL=false` (if an Organizations trail already exists).

### Bedrock model access
`preflight.py` picks the first invokable model from `model_preference` in `tenant.yaml`.
Anthropic models need the one-time use-case form in the Bedrock console; Amazon Nova works without it.

## CI/CD (GitHub Actions)
| Workflow | Trigger | AWS role | Does |
|---|---|---|---|
| `validate.yml` | every push / PR | none | fmt, validate, cfn-lint, unit tests, flow-graph check, Checkov (report only) |
| `plan.yml` | PR touching infra | `serve7-plan` (read-only, OIDC) | plans every layer, comments the summary on the PR |
| `deploy.yml` | merge to `main` / manual | `serve7-deploy` (OIDC, GitHub environment `<tenant>-<env>`) | applies all layers, syncs knowledge, runs smoke tests |

One-time GitHub setup: create environment `sandbox-dev` (Settings → Environments); add required reviewers for `*-prod`.
The OIDC trust in `bootstrap.yaml` only accepts that environment for deploys.

## Local checks
```bash
python3 -m pytest -q lambdas/genai_gateway/tests
for s in stacks/*/; do terraform -chdir=$s init -backend=false && terraform -chdir=$s validate; done
cfn-lint bootstrap/bootstrap.yaml
```

## Self-service (Amazon Q in Connect)
Layer 40 also deploys `modules/qic-selfservice/template.yaml` (CloudFormation, validated with cfn-lint):
Q in Connect assistant + custom knowledge base + Lex V2 bot with `AMAZON.QinConnectIntent` + Connect integrations.
`deploy.sh` then uploads `tenants/<tenant>/knowledge/*` with `scripts/sync-knowledge.py`, and validates the
rendered flow with `scripts/validate-flow.py` before layer 30 (shows Connect's real problem list).
Disable per tenant with `enable_self_service = false`.

## Not in this cut yet
Agentic (orchestrator + MCP tools) self-service, post-contact summary Lambda, VPC for Lambdas,
GitHub Actions workflows, flow export script, competency evidence pack.
