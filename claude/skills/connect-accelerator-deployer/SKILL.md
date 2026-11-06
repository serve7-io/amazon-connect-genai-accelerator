---
name: connect-accelerator-deployer
description: Set up, deploy and operate the Serve7 Amazon Connect + GenAI Deployment Accelerator (Terraform layers, bootstrap, CI/CD, AI agent) in your own GitHub org and AWS account, or onboard a new client tenant.
---

# Connect + GenAI Deployment Accelerator (full stack)

Use this skill to bring the accelerator in `serve7-io/amazon-connect-genai-accelerator` (or a copy of it) up in a new
GitHub org / AWS account, to onboard a client tenant, to change a tenant through PRs, or to debug a deploy.
The demo kit under `demos/` is a different system: use the `connect-acxd-demo-builder` skill for it.

Read in the repo before acting: `docs/start-here-with-claude.md` (a person starting from their own Claude account:
access checklist, prompts, costs, teardown), `docs/setup-new-org.md` (new org/account, technical steps),
`docs/runbook-new-tenant.md` (new tenant, production), `README.md`. They are the source of truth; this skill adds the
working rules and the lessons learned. The newest copy of this skill is in the repo at
`claude/skills/connect-accelerator-deployer/SKILL.md`; if it differs from the one you are running, tell the person to
re-upload it.

## Starting with someone new

When a person starts in their own Claude account (no project notes, no history), first walk them through the
checklist in `docs/start-here-with-claude.md` section 1, item by item, and ask which items are done. Things that are
not done and that only they can do (repo access, GitHub connector, linking the computer, AWS sign-in, budget alert)
come before any PR. Then:
1. Confirm the session is linked to their computer and run `python3 scripts/org-setup.py check --aws` in their clone.
2. If they have a Claude project, write a status note there (account, region, tenant, trust mode, open PRs) and keep it
   current after each step.

## 0. Ground rules (always)

- **Never ask for, read or print secrets.** No passwords, access keys, session tokens, deployment keys.
  - Admin passwords live in Secrets Manager (`s7-<tenant>-<env>/connect-user/<user>`); a person opens them.
  - If the person pastes credentials into chat anyway, tell them to revoke or rotate them.
- **What Claude must not do.** It does not:
  - create AWS accounts;
  - log in anywhere (`aws sso login`, console);
  - enter passwords;
  - change Identity Center;
  - edit `.github/workflows/` (the GitHub connector cannot);
  - change repo security settings.

  A person does these. Claude prepares the exact command or file.
- **Ask first** before:
  - deleting anything (`destroy.sh`, stacks, tenants);
  - anything that costs real money (phone numbers, provisioned streams in prod);
  - anything touching prod or a client account.
- **"Update branch" on a PR is a commit** to someone's branch: ask first.
- **Merging to `main` deploys.** `deploy.yml` applies the CI tenants (variable `CI_TENANTS` / `DEFAULT_TENANT`, else
  `sandbox`) on every push touching:
  - `stacks/`, `modules/`, `flows/`, `lambdas/`;
  - `tenants/`, `scripts/`, `bootstrap/`.

  Say so before merging. After a merge, ask the person to confirm the **deploy** run (not only `validate`) is green
  before merging the next infra PR. `demos/**`, `docs/**`, `claude/**` and `tools/**` never trigger it.
- **Never deploy the full accelerator into a shared or client account** unless that account is the tenant's own, the
  person confirmed it, and `tenant.yaml` names it. `deploy.sh` refuses a mismatched account; do not work around it.
- Synthetic data only in demo tenants (`demo-crm.json`, knowledge). The repo may be public: no keys, no real customer data.

## 1. Architecture (one tenant = one AWS account per environment)

```
bootstrap/bootstrap.yaml (stack serve7-bootstrap, once per account)
  GitHub OIDC -> serve7-deploy (environment <t>-<env> or main) / serve7-plan (PRs), permissions boundary,
  state bucket serve7-tfstate-<acct>-<region>, CloudTrail
stacks/10-foundation   KMS, recordings/knowledge/artifacts buckets
stacks/20-connect-core Connect instance (alias s7-<t>-<last6>), storage, Kinesis CTR + agent events
stacks/40-genai        guardrail, genai-gateway Lambda (Bedrock), contact state, Q in Connect self-service (CFN
                       modules/qic-selfservice), agentic tools (CRM table, tools Lambda, AgentCore MCP gateway),
                       AI agent (Connect MCP integration, security profile, ORCHESTRATION agent + version)
stacks/30-routing      hours, queues, routing profiles, inbound flow (flows/*.tftpl), phone number, admin users
stacks/50-observability SNS, alarms, dashboard
Apply order 10 -> 20 -> 40 -> 30 -> 50 (the flow needs the gateway ARN). deploy.sh does all of it.
```

| Piece | Where |
|---|---|
| Tenant config | `tenants/<t>/`: `tenant.yaml` (account, region, alias, models, `environments.prod`), `<env>.tfvars`, `routing.yaml`, `agent.yaml`, `knowledge/`, `demo-crm.json` |
| CI | `.github/workflows/` (installed); reviewed sources are in `ci/workflows/` |
| Scripts | `scripts/`: `ci-tenants.py` (matrix), `org-setup.py` (readiness), `new-tenant.py`, `check-tenants.py`, `tenant-info.py`, `preflight.py`, `validate-flow.py`, `sync-knowledge.py`, `seed-demo-crm.py`, `configure-ai-agent.py`, `fetch-orchestration-prompt.py` |

## 2. New GitHub org / AWS account (follow docs/setup-new-org.md)

1. **Repo** (person):
   - import, fork, or push a git copy into an empty private repo (commands in `docs/start-here-with-claude.md`);
     keep it private;
   - install the Claude GitHub connector;
   - clone with git: `deploy.sh` takes the CI trust from `origin`.
2. **Workflows** (person): `bash scripts/install-workflows.sh --apply` and push, or paste `ci/workflows/*.yml` in the
   web editor. Claude only edits `ci/workflows/` in PRs.
3. **Repository variables** (person, or `python3 scripts/org-setup.py github --tenant <t> [--no-environments]
   [--not-bootstrapped] --apply` on their machine):

   | Variable | Value |
   |---|---|
   | `AWS_CI` | `false` until bootstrapped |
   | `DEFAULT_TENANT` | `<t>` |
   | `DEFAULT_ENV` | `dev` |
   | `USE_ENVIRONMENTS` | `false` on a free private repo |

4. **AWS access** (person):
   - root MFA;
   - Identity Center admin for people;
   - optional `ClaudeOperator` permission set: AdministratorAccess + `docs/claude-operator-policy.json`;
   - Claude desktop linked to the computer;
   - `aws configure sso` / `aws sso login`, then restart the app.
5. **Scaffold** (Claude, PR):
   ```bash
   python3 scripts/new-tenant.py --tenant <t> --account-id <12> --region <r> --company "<name>" --owner-email <email> [--prod-account-id <12>]
   python3 scripts/check-tenants.py
   ```
   Then edit `routing.yaml` (time zone!), `knowledge/`, `agent.yaml`, `dev.tfvars`. Keep
   `claim_phone_number = false` until the phone quota is raised.
6. **Readiness** (Claude): `python3 scripts/org-setup.py check --aws` (or `--json`). Fix every `[todo]`. Before the first
   deploy only "account not bootstrapped" may remain.
7. **First deploy** (person, admin credentials in that account, ~10 min). Long-running, so do not try to run it in a
   short tool shell. Give the person:
   ```bash
   AUTO_APPROVE=1 ./scripts/deploy.sh <t> dev                            # GitHub environments
   AUTO_APPROVE=1 DEPLOY_TRUST=main-branch ./scripts/deploy.sh <t> dev   # free private repo
   SMOKE_SKIP=phone,chat python3 tests/smoke.py --tenant <t>
   ```
   Brand-new account and Bedrock says "under verification": add `SKIP_MODEL_TEST=1` and keep `amazon.nova-lite`.
8. **CI on** (person):
   - create environment `<t>-dev` (if environments are used);
   - set `AWS_CI=true`;
   - Actions → deploy → Run workflow;
   - confirm green;
   - `org-setup.py check --aws` must show no `[todo]`.

## 3. Day-to-day changes (Claude)

- Every change is a PR: branch → push → PR → `validate` (static) + `plan` (Terraform plan comment) → merge → CI deploy.
- **Push with the GitHub connector:**
  - `create_branch`, then `push_files` (new files) or `create_or_update_file` with the blob sha (existing files).
  - `create_or_update_file` **keeps the executable bit** of an existing script; `push_files` creates new files as 100644.
    Document new scripts as `bash scripts/x.sh` / `python3 scripts/x.py`.
  - Verify every push: `git fetch origin <branch> && git show FETCH_HEAD:<path> | cmp - <local>`, and
    `git ls-tree FETCH_HEAD scripts/deploy.sh` must still be `100755`.
  - A `\uXXXX` escape (for example the line separator U+2028) in a file you push can arrive as the real character.
    Build such characters with `chr(0x2028)` in the source, and keep the literal character out of pushed files.
- **Local checks before pushing:**
  ```bash
  python3 -m pytest -q tests/unit lambdas/genai_gateway/tests lambdas/agent_tools/tests
  python3 -m pytest -q cloudformation/connect-phase1/tests
  python3 scripts/check-tenants.py
  cfn-lint bootstrap/bootstrap.yaml modules/*/template.yaml cloudformation/*/template.yaml
  tofu fmt -check -recursive stacks modules
  for s in stacks/*/; do [ "$s" = stacks/_shared/ ] && continue; tofu -chdir=$s init -backend=false -input=false >/dev/null && tofu -chdir=$s validate; done
  ```
  Notes on these checks:
  - Terraform downloads may be blocked in the Claude sandbox; OpenTofu (`tofu`) reproduces fmt/validate.
  - Delete the `.terraform*` files it leaves behind; never commit lock files from tofu.
  - `stacks/_shared` is not a root module.
  - When `ci/workflows/` changes, also run `actionlint ci/workflows/*.yml`.
- Stacked PRs: retarget each one to `main` after its base merges. Changing the base does not re-run checks; a new commit
  does, and that commit needs the person's OK. Merge in order and check the deploy run between infra merges.
- After changing anything Connect rejects at deploy time, run `scripts/validate-flow.py --tenant <t>`. It creates the
  flow as a throwaway and prints Connect's real problem list.

## 4. Hard-won facts (each cost time)

- **AgentCore MCP gateway JWT:**
  - the allowed audience must be the gateway's own id, and the MCP protocol version `2025-03-26`;
  - the id exists only after create, so `deploy.sh` applies layer 40 a second time on the first create;
  - "Target entity ..." errors mean this.
- **Connect MCP integration** makes Connect add a policy to its service-linked role. The bootstrap boundary must allow
  it (statement `ConnectServiceLinkedRolePolicies`). Older bootstraps need `REBOOTSTRAP=1 ./scripts/deploy.sh <t> <env>` once.
- **AI agent security profiles** are linked per agent ARN: unqualified, `:$LATEST` and `:N`.
  `scripts/configure-ai-agent.py` does it and sets the assistant's ORCHESTRATION / `Connect.SelfService` default.
- **Stack tags must not change** on Wisdom / Connect-integration CloudFormation stacks (`AWS::Wisdom::KnowledgeBase`,
  `AssistantAssociation` tags are not updatable): a default-tag change rolled `qic-selfservice` back.
  - All `aws_cloudformation_stack` resources use `ignore_changes = [tags, tags_all]`; `scripts/check-stack-tags.py`
    guards it.
  - A stale `UPDATE_ROLLBACK_COMPLETE` clears on the next successful update.
- **Terraform `validate` evaluates resources even at `count = 0`.** Guard optional file-backed locals with `try()`. Example:
  `local.base_prompt` is null until `fetch-orchestration-prompt.py` has written its file.
- **Persona:** an `agent.yaml` persona only takes effect after `scripts/fetch-orchestration-prompt.py --tenant <t>` has
  written `modules/ai-selfservice-agent/prompts/orchestration-chat.base.json` (commit it). Before that the AWS prompt is
  used and Terraform warns.
- **Bedrock in new accounts:**
  - "under verification" for hours: `SKIP_MODEL_TEST=1`;
  - Anthropic models need the use-case form;
  - Nova works at once;
  - `preflight.py` picks the first invokable model from `model_preference`.
- **Connect in new accounts:** one instance per account, and the phone-number quota is 0. Request it, and keep
  `claim_phone_number = false`.
- **Alias:** Connect aliases are unique per region across all of AWS; the default is `s7-<t>-<last 6 of account>`.
- **Accounts and environments:**
  - One environment per account: `deploy.sh` refuses a second.
  - Prod from a workstation needs `PROD_CONFIRM=<t>`.
  - Prod defaults: SAML (fixed at instance creation), `ON_DEMAND` streams, retention of at least 90 days, guardrail IAM
    enforced, invocation logging off. `check-tenants.py` enforces them.
- **GitHub trust:**
  - `deploy.sh` derives `GitHubOrg`/`GitHubRepo` from `origin` on the first bootstrap, and stops when there is no remote
    (zip).
  - After a repo transfer or copy: `RETRUST=1 ./scripts/deploy.sh <t> <env>`.
  - Repos with immutable OIDC subjects need `GitHubSubjectPrefix`. `deploy.sh` reads it with `gh`, or set
    `GITHUB_SUBJECT_PREFIX`.
  - `DeployTrust=environment` needs GitHub environments (paid plan for private repos). Otherwise use `main-branch` with
    `USE_ENVIRONMENTS=false`.
- **CloudShell:** home is 1 GB. `deploy.sh` keeps providers in `/tmp`. Sessions time out: rerunning `deploy.sh` is safe.
- **Demo CRM seeding is a script** (`seed-demo-crm.py`), not Terraform, so the read-only plan role never decrypts table data.
- **Data tables** (`tenants/<t>/data-tables.yaml`, `scripts/sync-data-tables.py`, see `docs/data-table-manager.md`). The
  bootstrap boundary needs the data-table actions: accounts bootstrapped before PR #95 need `REBOOTSTRAP=1` once;
  new accounts get them on the first bootstrap.
- **Costs:** a dev tenant costs roughly 25–40 USD a month (two provisioned Kinesis streams ~11 USD each, KMS keys
  1 USD each, dashboard, phone number if claimed, Bedrock/Contact Lens usage). Say so before the first deploy and
  suggest a budget alert (the person sets it; Claude does not touch billing).
- **Teardown:** `AWS_CI=false` first, then `./scripts/destroy.sh <t> <env>` (person, types the tenant name), then
  `bash tools/account-shutdown.sh` (inventory) and `--delete` (person, types the account id) for leftovers. It keeps
  `serve7-bootstrap`. Deleting a credit card does not stop charges.
- Temporary console changes (for example opening hours for a demo) are overwritten by the next layer-30 deploy. Record
  them and restore them.

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| CI: `Not authorized to perform sts:AssumeRoleWithWebIdentity` | Trust names another repo, org, prefix or environment mode: `org-setup.py check --aws`, then `RETRUST=1`, or align `USE_ENVIRONMENTS` / `DEPLOY_TRUST` |
| Plan/deploy runs for `sandbox` in a copy | Workflows not installed from `ci/workflows/`, or `DEFAULT_TENANT` unset |
| `deploy.sh`: account mismatch | Wrong credentials: `eval "$(scripts/assume-tenant.sh <acct>)"` (Organizations) or switch the SSO profile |
| Layer 30 fails with `InvalidContactFlowException` | Run `scripts/validate-flow.py` to get Connect's real problem list; edit only `${...}` placeholders in `flows/*.tftpl` |
| Gateway "Target entity" / tool calls unauthorized | Gateway audience/version (section 4); rerun the deploy (second pass) |
| MCP integration association AccessDenied | Bootstrap without `ConnectServiceLinkedRolePolicies`: `REBOOTSTRAP=1` |
| `UPDATE_ROLLBACK_COMPLETE` on `qic-selfservice` | Tag change on a Wisdom stack; fixed by `ignore_changes`; the next deploy clears it |
| `static` fails only on Terraform steps | `tofu fmt -check` / `tofu validate` locally; null attribute access → `try()` |
| Bedrock model not found / access denied | Use-case form, region, `SKIP_MODEL_TEST=1` for new accounts |
| Phone number claim fails | Quota 0: `claim_phone_number = false` |
| Smoke chat test fails from CI | Expected: API chats never subscribe a websocket. CI runs with `SMOKE_SKIP=phone,chat` |

## 6. Reporting

After each step, tell the person:
- what changed;
- what deploys on merge;
- what they must click or run themselves.

Keep a status doc in the Claude project, like `claude/sandbox-status.md` and `claude/open-prs-review.md`. Record:
- tenants;
- accounts;
- trust state;
- open PRs;
- temporary changes;
- follow-ups.
