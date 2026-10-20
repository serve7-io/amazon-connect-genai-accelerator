# Serve7 Amazon Connect + GenAI Deployment Accelerator

Terraform + GitHub Actions framework that stands up Amazon Connect with a guarded Amazon Bedrock
layer in a fresh AWS account. Design and rationale: see the Implementation Guide (claude.ai doc).

## Layout
| Path | What |
|---|---|
| `bootstrap/bootstrap.yaml` | One-time CloudFormation per account: GitHub OIDC, `serve7-deploy`/`serve7-plan` roles, permissions boundary, state bucket, CloudTrail |
| `stacks/10-foundation` | KMS keys, recordings / knowledge / artifacts buckets |
| `stacks/20-connect-core` | Connect instance, storage config, CTR + agent-event Kinesis streams |
| `stacks/40-genai` | Bedrock guardrail, gateway Lambda, contact-state table, invocation logging, Q in Connect self-service, agentic tools + AI agent |
| `stacks/30-routing` | Hours, queues, routing profile, inbound flow, phone number, admin user |
| `stacks/50-observability` | SNS, alarm baseline, dashboard |
| `modules/` | Shared building blocks: Terraform (`kms-key`, `lambda-function`, `s3-secure-bucket`) and CloudFormation (`qic-selfservice`, `agent-tools-gateway`, `ai-selfservice-agent`) |
| `flows/` | Contact flow templates (`${...}` placeholders resolved by Terraform) |
| `lambdas/genai_gateway` | Connect -> Bedrock gateway (+ prompts, unit tests) |
| `lambdas/agent_tools` | MCP tools behind the AgentCore Gateway: `customer_lookup`, `retrieve_status`, `create_support_ticket` (+ unit tests) |
| `tenants/<tenant>/` | All per-client config: `tenant.yaml`, `<env>.tfvars`, `routing.yaml`, `knowledge/` (sandbox also has `demo-crm.json`) |
| `templates/tenant/` | Scaffold used by `scripts/new-tenant.py` |
| `scripts/` | `deploy.sh`, `destroy.sh`, `preflight.py`, `new-tenant.py`, `check-tenants.py`, `tenant-info.py`, `sync-knowledge.py`, `validate-flow.py`, `configure-ai-agent.py`, `seed-demo-crm.py`, `adopt-instance.sh`, `assume-tenant.sh` |
| `tests/smoke.py` | Post-deploy checks |
| `docs/runbook-new-tenant.md` | Onboarding a new client tenant, step by step |
| `cloudformation/connect-phase1/` | Standalone single-stack Connect instance (deploy / verify / tear down with `phase1.sh`), independent of the Terraform layers |
| `demos/` | Self-contained client demos, deployed separately from the tenant layers (see [Demos](#demos)) |

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
| `validate.yml` | every push / PR | none | fmt, validate, cfn-lint, tenant-folder check, unit tests (gateway, agent tools, Phase 1 tools), flow-graph check, Checkov (report only) |
| `plan.yml` | PR touching infra | `serve7-plan` (read-only, OIDC) | plans every layer, comments the summary on the PR |
| `deploy.yml` | merge to `main` / manual | `serve7-deploy` (OIDC, GitHub environment `<tenant>-<env>`) | applies all layers, syncs knowledge, runs smoke tests |
| `demo.yml` | issue titled `demo: <command> <demo> [args]` / manual / PR touching `demos/**` | demo CI role | deploy, delete, status, seed, reset, test, web (CloudFront test page) and admin commands for the demos under `demos/` |
| `ccg-s5.yml` | issue titled `ccg-s5: <command>` / manual / PR or push touching `demos/ccg-s5/**` | demo CI role | tests, deploy and day-to-day operations for the CCG Scenario 5 demo |
| `auto-ship.yml` | push of a branch named `auto/**` by the owner | `GITHUB_TOKEN` only (the deploy itself runs in `demo.yml`) | merges the branch into `main` when it only touches `demos/` (not `ccg-s5`), `docs/` or `README.md` and all demo/kit checks pass, deletes the branch, then dispatches `demo.yml deploy <demo>` for changed demos in `AUTO_DEPLOY`; anything else gets a review PR |

One-time GitHub setup: create environment `sandbox-dev` (Settings → Environments); add required reviewers for `*-prod`.
The OIDC trust in `bootstrap.yaml` only accepts that environment for deploys.

## Local checks
```bash
python3 -m pytest -q lambdas/genai_gateway/tests lambdas/agent_tools/tests
python3 -m pytest -q cloudformation/connect-phase1/tests
python3 scripts/check-tenants.py
for s in stacks/*/; do [ "$s" = "stacks/_shared/" ] && continue; terraform -chdir=$s init -backend=false && terraform -chdir=$s validate; done
cfn-lint bootstrap/bootstrap.yaml modules/*/template.yaml cloudformation/*/template.yaml
```

## Self-service (Amazon Q in Connect)
Layer 40 also deploys `modules/qic-selfservice/template.yaml` (CloudFormation, validated with cfn-lint):
Q in Connect assistant + custom knowledge base + Lex V2 bot with `AMAZON.QinConnectIntent` + Connect integrations.
`deploy.sh` then uploads `tenants/<tenant>/knowledge/*` with `scripts/sync-knowledge.py`, and validates the
rendered flow with `scripts/validate-flow.py` before layer 30 (shows Connect's real problem list).
Disable per tenant with `enable_self_service = false`.

## Agentic self-service (orchestrator + MCP tools)
Also in layer 40, on top of self-service:

1. `agentic.tf` + `modules/agent-tools-gateway`: demo CRM DynamoDB table -> `lambdas/agent_tools` -> Bedrock AgentCore Gateway (MCP).
2. `agent.tf` + `modules/ai-selfservice-agent`: Connect MCP integration, AI security profile, orchestration AI agent (knowledge base, CRM tools, escalate/complete) and a published version.
3. `deploy.sh` finishes the pieces CloudFormation has no resource for: `scripts/seed-demo-crm.py` loads `tenants/<tenant>/demo-crm.json`, and `scripts/configure-ai-agent.py` links the security profile and makes the newest version the assistant's self-service agent. The gateway id is only known after the first apply, so `deploy.sh` does a second pass.

| Variable | Default | Meaning |
|---|---|---|
| `enable_self_service` | `true` | Q in Connect + Lex self-service |
| `enable_agentic_tools` | `false` (sandbox: `true`) | Tools Lambda, gateway and demo CRM table |
| `enable_ai_agent` | `true` | AI agent; needs `enable_agentic_tools` and `enable_self_service` |
| `demo_crm_file` | `""` | Seed file of fictional customers (sandbox/demo tenants only) |

## Demos
`demos/` holds self-contained client demos. They are CloudFormation stacks named `demo-*` and are deployed and operated
through GitHub issues or Actions (`demo.yml`, `ccg-s5.yml`), not through the tenant layers. **Synthetic data only.** Each has its own README.

| Demo | What |
|---|---|
| `demos/_kit` | Shared kit: deploy / delete / smoke scripts, seed + data-request helpers, the CloudFront web test pages (`web/`), and the layout every demo follows |
| `demos/yamaha-moto` | ACXD chat agent for a motorcycle rental (fleet, availability, quotes, bookings) |
| `demos/renault-rental` | ACXD chat agent for a car rental (fleet, availability, quotes, bookings, category B licence check) |
| `demos/ccg-s1` | Persistent cross-channel roadside assistance (AI chat -> live chat -> voice) |
| `demos/ccg-s5` | Secure renewal payment over voice with a simulated PCI partner hand-over |

## Not in this cut yet
Post-contact summary Lambda, VPC for Lambdas, a flow export script for the Terraform inbound flow
(`cloudformation/connect-phase1/templatize-flow.py` covers the Phase 1 stack only), competency evidence pack.
