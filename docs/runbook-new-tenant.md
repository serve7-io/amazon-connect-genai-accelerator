# Runbook: onboard a new client tenant

Goal: a new client gets a production-shaped Amazon Connect + GenAI environment from this repo, in one sitting,
with no console click-ops. Every step below is scripted and repeatable.

| Step | What | Who | Typical time |
|------|------|-----|--------------|
| 0 | AWS account for the client (Organizations member account) | Serve7 cloud admin | 2–5 min |
| 1 | Scaffold the tenant folder | Delivery engineer | 2 min |
| 2 | Fill in client specifics (hours, queues, messages, knowledge) | Delivery engineer + client | 15–60 min |
| 3 | First deploy (bootstrap + 5 layers) | Delivery engineer | 15–25 min, mostly waiting |
| 4 | Smoke tests | Automated | 1 min |
| 5 | Enable CI/CD for the tenant | Delivery engineer | 2 min |
| 6 | Hand-over checks | Delivery lead | 15 min |

Step 3's time was measured on the `client-demo` reference deployment; see "Evidence" at the end.

---

## 0. Client AWS account

Run this from the Serve7 Organizations management account, in CloudShell:

```bash
REQ=$(aws organizations create-account --email "<unique-email>" --account-name "serve7-<client>" \
  --role-name OrganizationAccountAccessRole --query "CreateAccountStatus.Id" --output text)
aws organizations describe-create-account-status --create-account-request-id $REQ \
  --query "CreateAccountStatus.[State,AccountId]" --output text      # repeat until SUCCEEDED
```

A Connect instance needs its own account. New accounts allow 1 instance, which is why there is one tenant per account.
If the client brings their own account, ask them for a role you can assume, and use it as `ROLE=<name>` in step 3.

## 1. Scaffold

```bash
python3 scripts/new-tenant.py --tenant <client> --account-id <12 digits> --region ca-central-1 \
  --company "<Client legal name>" --owner-email <delivery-owner@serve7.io> [--timezone America/Toronto] [--industry retail]
python3 scripts/check-tenants.py          # same gate CI runs
```

This creates `tenants/<client>/` with `tenant.yaml`, `dev.tfvars`, `routing.yaml` and `knowledge/faq.txt`. The scaffolder:
- refuses to overwrite an existing tenant;
- refuses to reuse an account;
- validates names.

## 2. Client specifics

| File | Edit |
|------|------|
| `routing.yaml` | Hours (time zone!), queues, routing profiles, customer-facing messages (AI disclosure is mandatory) |
| `knowledge/*.txt` | The client's FAQ or knowledge articles, one topic per paragraph. Synced to Q in Connect on every deploy |
| `tenant.yaml` | `model_preference` or a pinned `bedrock_model_id`, cost centre, ACE opportunity id |
| `dev.tfvars` | Admin users, alarm emails, `claim_phone_number`, `enable_agentic_tools` |

Commit on a branch and open a PR. CI validates the folder, and once step 5 is done it posts a plan.

## 3. First deploy (from CloudShell in the management account)

```bash
cd ~/acc && git pull
eval "$(scripts/assume-tenant.sh <account-id>)"        # 1-hour session in the client account
AUTO_APPROVE=1 ./scripts/deploy.sh <client> dev
```

`deploy.sh` does the following:
- **Refuses to run if the credentials are for a different account than `tenant.yaml`.**
- Bootstraps the account:
  - GitHub OIDC trust, derived from the git remote and `gh`;
  - deploy and plan roles;
  - permissions boundary;
  - state bucket;
  - CloudTrail.
- Runs pre-flight: region, root MFA, Bedrock model discovery.
- Applies the layers 10 → 20 → 40 → 30 → 50, validating the contact flow before layer 30 and syncing knowledge after layer 40.

If Bedrock says the account is still being verified (common for brand-new accounts), keep the pinned Nova model and run
`SKIP_MODEL_TEST=1 ...` again later.

## 4. Smoke tests

```bash
SMOKE_SKIP=phone,chat python3 tests/smoke.py --tenant <client>
```

This checks:
- the instance, storage, flow and placeholders;
- the GenAI gateway: classification + guardrail;
- contact state;
- Q in Connect knowledge answers.

All must pass before hand-over.

## 5. CI/CD for the tenant

```bash
gh api -X PUT repos/<owner>/<repo>/environments/<client>-dev
gh workflow run deploy -f tenant=<client> -f env=dev       # first pipeline deploy, OIDC into the client account
```

From now on, changes reach the client only through PR → plan → merge → deploy.

## 6. Hand-over checklist

- [ ] Smoke tests green in CI for `<client>-dev`
- [ ] Root MFA on in the client account; no IAM users with access keys
- [ ] Admin user logged in and password changed (initial one is in Secrets Manager `s7-<client>-dev/connect-user/<user>`)
- [ ] Test chat: AI disclosure shown, a knowledge question answered, routing to the billing queue works
- [ ] Alarm emails confirmed (SNS subscription confirmation clicked)
- [ ] Phone number quota requested, if voice is in scope
- [ ] Competency evidence captured (screenshots + `tests/smoke.py` output) into the evidence pack

## Production environment

Production is a **separate AWS account** for the same tenant. `deploy.sh` refuses to put two environments in one account,
because state keys and the bootstrap stack are per account.

1. Create the prod account (step 0), then scaffold with both accounts:
   ```bash
   python3 scripts/new-tenant.py --tenant <client> --account-id <dev> --prod-account-id <prod> --company "..." --owner-email ...
   ```
   Or add the following to an existing `tenant.yaml`, then copy `templates/tenant/prod.tfvars.optional` to `prod.tfvars` and fill in the values:
   ```yaml
   environments:
     prod:
       account_id: "<prod>"
       instance_alias: s7-<client>-prod-<last6>
   ```
2. Defaults in `prod.tfvars`, enforced by `check-tenants.py` in CI:

   | Setting | Prod value | Why |
   |---|---|---|
   | `identity_management_type` | `SAML` | Agents sign in via the client IdP. **Fixed at instance creation.** Local users need a written `# allow_local_users_in_prod` waiver |
   | `enforce_guardrail_iam` | `true` | Every model call must carry the tenant guardrail |
   | `kinesis_stream_mode` | `ON_DEMAND` | Scales with real traffic |
   | `recording_retention_days` | 365 (≥ 90 enforced) | Set from the client contract |
   | `enable_invocation_logging` | `false` | Prompts contain PII; enable only with written client approval |
   | `data_classification` | `confidential` | Tags every resource |

3. Deploy production:
   - **CI only:** create the GitHub environment `<client>-prod` **with required reviewers**. This needs GitHub Pro/Team or the Serve7 org.
   - Then run `gh workflow run deploy -f tenant=<client> -f env=prod`.
   - Bootstrap the prod account once, from an admin session: `PROD_CONFIRM=<client> AUTO_APPROVE=1 ./scripts/deploy.sh <client> prod`.
     Without `PROD_CONFIRM`, `deploy.sh` refuses a local prod deploy.
4. `destroy.sh` never destroys prod.

## Agentic self-service (optional, `enable_agentic_tools = true`)

Everything is code; no console steps.

| Piece | Where |
|---|---|
| Demo CRM table, tools Lambda, AgentCore MCP gateway (JWT: Connect discovery URL + gateway id as audience, MCP 2025-03-26) | `stacks/40-genai/agentic.tf`, `modules/agent-tools-gateway` |
| Connect MCP integration, AI security profile (3 CRM tools + `Wisdom.View`), ORCHESTRATION AI agent + version | `stacks/40-genai/agent.tf`, `modules/ai-selfservice-agent` |
| Profile linked to the agent; newest version set as the assistant's self-service agent | `scripts/configure-ai-agent.py` (run by `deploy.sh`) |

On a gateway's first create, `deploy.sh` applies layer 40 twice: the gateway id only exists after create, and it is its own JWT audience.
The agent may only state account or order facts a CRM tool returned (tool instructions); on tool errors it offers a human.

Test: chat "I'm customer C1001, where is my order?" should answer with order 5001 (shipped) and 5002 (processing) from `demo-crm.json`.

## Removing a tenant

```bash
eval "$(scripts/assume-tenant.sh <account-id>)" && ./scripts/destroy.sh <client> dev
```

Then close the member account in Organizations.

---

## Evidence: client-demo reference deployment (2026-10-07)

Fictional client "Northwind Demo Inc", brand-new Organizations member account 915626646645, ca-central-1.

| Step | Duration | Result |
|------|----------|--------|
| 0 Account (`organizations create-account`) | ~1 min | SUCCEEDED |
| 1 Scaffold (`new-tenant.py`) + `check-tenants.py` | < 1 min | 4 files, all checks pass |
| 3a Bootstrap (inside `deploy.sh`) | ~1 min | OIDC trust, deploy/plan roles, boundary, state bucket, CloudTrail |
| 3b Layers 10 → 50 (`deploy.sh`) | **6 min 45 s** | 74 resources: 27 + 7 + 21 + 10 + 9, knowledge synced, no manual fixes |
| 4 Smoke tests | ~30 s | **11/11 pass**: instance, storage, flow, GenAI routing 0.95, guardrail blocks injection, Q in Connect answers |
| 5 CI pipeline deploy (`gh workflow run deploy -f tenant=client-demo`) | 3 min 17 s | OIDC into the client account, all layers no-op, smoke green |

**From empty AWS account to a tested Connect + GenAI contact centre: about 10 minutes of machine time.**

Guards that fired during the run, both working as designed:
- `deploy.sh` refused to deploy client-demo with sandbox credentials (account guard).
- Pre-flight stopped on "Bedrock account being verified". The run continued with the pinned model via `SKIP_MODEL_TEST=1`; verification cleared before the smoke tests ran.

Operator notes:
- Root cannot assume roles. Onboard from an IAM Identity Center admin session (permission set `Serve7-Admin`), or open the console and CloudShell in the member account directly from the access portal.
- Every browser tab or account has its own CloudShell home. Install `gh` and clone the repo once per identity.
- Member accounts created by Organizations have no root password. Enable Organizations "centralized root access" so the MFA warning can be cleared.
