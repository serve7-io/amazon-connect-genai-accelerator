# Set up the accelerator in a new GitHub org and AWS account

Use this guide to run the full accelerator from **your own copy of this repo and your own AWS account(s)**. It covers the
bootstrap, layers 10–50, CI/CD and the AI agent. It is written so that a person and an AI assistant (Claude in Cowork) can
do it together: every step says who does it.

- New client tenant inside an org that is already set up: use [runbook-new-tenant.md](runbook-new-tenant.md) instead.
- Demo kit (`demos/`): it has its own CI trust; see [The two CI trusts](#the-two-ci-trusts) and `demos/_kit/`.

**Time:** about 1 hour, mostly waiting. The deploy itself takes about 10 minutes.

**What you end up with:** Amazon Connect + guarded Bedrock gateway + Q in Connect self-service (+ optional MCP-tool AI
agent) in your account. CI plans every PR and deploys every merge to `main`.

## Who does what

| Step | Person | Claude |
|---|---|---|
| Copy the repo, install the GitHub connector | ✔ | |
| Install workflow files (`.github/workflows/`) | ✔ (connectors cannot write workflows) | prepares them in `ci/workflows/` |
| Repository variables / environments | ✔ (or `gh` on your computer) | prints the commands |
| AWS account, Identity Center, `aws sso login` | ✔ (Claude must not create accounts or log in) | |
| Scaffold and edit the tenant folder | review | ✔ (PR) |
| Readiness checks | | ✔ `scripts/org-setup.py check --aws` |
| First deploy (bootstrap needs admin rights, ~10 min) | ✔ CloudShell or laptop | prepares the exact command |
| Every later change | review + merge | ✔ PR → plan → merge → CI deploy |
| Read passwords or secret values | ✔ only | never |

## 0. Prerequisites

- An AWS account for each environment: dev, plus prod later. A Connect instance needs its own account, and new accounts
  allow one instance.
  - Organizations is optional. A single standalone account works: skip `assume-tenant.sh` and use an admin session
    directly in that account.
- A region with Amazon Connect, Amazon Q in Connect and Bedrock, for example `ca-central-1` or `us-east-1`. The list is
  in `scripts/new-tenant.py`.
- A GitHub org or user with Actions enabled.
  - **Private repos on GitHub Free have no environments.** Use `USE_ENVIRONMENTS=false` together with
    `DEPLOY_TRUST=main-branch`.
- On your computer: git, Python 3.10+, AWS CLI v2. `gh` is optional but saves clicks. `deploy.sh` installs Terraform
  1.10.5 itself on Linux; on Windows use CloudShell for the deploy.

## 1. Copy the repo (person)

1. GitHub → **Import repository** (or fork) into your org. Keep it **private**: tenant files name accounts and emails.
2. Install the **Claude GitHub connector** on the new repo, so Claude can open branches and PRs.
3. Clone it with **git** (not a zip): `deploy.sh` reads the GitHub org and repo for the CI trust from the `origin` remote.

## 2. Install the workflows (person)

The reviewed workflow sources live in `ci/workflows/` and select tenants through repository variables:
`deploy.yml`, `plan.yml` and `validate.yml`. Install them once, and again whenever `ci/workflows/` changes in a PR:

```bash
bash scripts/install-workflows.sh            # shows the diff
bash scripts/install-workflows.sh --apply    # copies into .github/workflows and commits; then push
```

Without push rights for workflows, open each `.github/workflows/<name>.yml` in the GitHub web editor and paste the
`ci/workflows/<name>.yml` content. `validate` warns while they differ.

## 3. Repository variables (person, before any AWS work)

Settings → Secrets and variables → Actions → **Variables**:

| Variable | Value | Effect |
|---|---|---|
| `AWS_CI` | `false` until step 7, then `true` | `plan` and push-triggered `deploy` are skipped while `false` (no failures before the bootstrap) |
| `DEFAULT_TENANT` | your tenant name, e.g. `acme` | the tenant CI plans on PRs and deploys on merge (default `sandbox` = Serve7's account) |
| `DEFAULT_ENV` | `dev` | environment for `DEFAULT_TENANT` |
| `CI_TENANTS` | optional, e.g. `acme/dev beta/dev` | several tenants instead of `DEFAULT_TENANT`; prod is never deployed on push |
| `USE_ENVIRONMENTS` | `true` (GitHub environments) or `false` (free private repo) | must match the bootstrap's `DEPLOY_TRUST` (`environment` / `main-branch`) |

The repo also ships Serve7's tenants: `sandbox` and `client-demo` point at Serve7's accounts, and `northwind-moto` and
`bowriver-dental` use placeholder accounts. CI ignores every tenant that is not in `DEFAULT_TENANT` or `CI_TENANTS`, so you
can keep them or delete them in your copy.

After step 5, `python3 scripts/org-setup.py github --tenant <t> [--no-environments] [--not-bootstrapped]` prints the
matching `gh variable set ...` commands (`--apply` runs them).

## 4. AWS access (person)

1. Root user: MFA on, no access keys.
2. Sign-in for people: IAM Identity Center with an admin permission set, used for the first deploy.
3. Sign-in for Claude (optional): a permission set `ClaudeOperator`.
   - It combines **AdministratorAccess** with the inline deny policy [`claude-operator-policy.json`](claude-operator-policy.json).
   - The deny covers: account closure and creation, Identity Center changes, CloudTrail tampering, IAM users, access
     keys and passwords, and billing.
   - Assign it on the tenant account(s). Session length: 8–12 h.
4. In the Claude desktop app, link the session to your computer. Then:
   - `aws configure sso` (profile `default`, the ClaudeOperator role);
   - `aws sso login`;
   - restart the app after logging in.

   Claude never asks for keys or passwords. If a check says "no working AWS credentials", log in again.
5. Bedrock:
   - Amazon Nova works without forms.
   - Anthropic models need the one-time use-case form in the Bedrock console.
   - A brand-new account can be "under verification" for a few hours: use `SKIP_MODEL_TEST=1` meanwhile.
6. Voice: new accounts have a phone-number quota of 0. Request an increase and keep `claim_phone_number = false` until then.

## 5. Scaffold your tenant (Claude, PR)

```bash
python3 scripts/new-tenant.py --tenant acme --account-id 123456789012 --region ca-central-1 \
  --company "Acme Inc" --owner-email ops@acme.example [--timezone America/Toronto] [--prod-account-id 210987654321]
python3 scripts/check-tenants.py
```

Then edit these files (what each controls is in [runbook-new-tenant.md](runbook-new-tenant.md), step 2):

- `tenants/acme/routing.yaml`: hours, queues and messages;
- `knowledge/`;
- `agent.yaml`;
- `dev.tfvars`: admin user, alarm emails, `enable_agentic_tools`.

Open a PR and merge it once `validate` is green. With `AWS_CI=false` nothing touches AWS yet.

## 6. Readiness check (Claude or person)

```bash
python3 scripts/org-setup.py check          # repo, tenants, workflows, GitHub variables
python3 scripts/org-setup.py check --aws    # + the account: credentials, bootstrap, CI trust, failed stacks, Connect
```

Every `[todo]` line comes with the exact fix. Before the first deploy the only expected `[todo]` is "account not bootstrapped".

## 7. First deploy (person, admin session in the tenant account, from a git clone)

```bash
# Organizations: eval "$(scripts/assume-tenant.sh 123456789012)"     standalone account: skip this line
AUTO_APPROVE=1 ./scripts/deploy.sh acme dev                            # GitHub environments (USE_ENVIRONMENTS=true)
AUTO_APPROVE=1 DEPLOY_TRUST=main-branch ./scripts/deploy.sh acme dev   # free private repo (USE_ENVIRONMENTS=false)
SMOKE_SKIP=phone,chat python3 tests/smoke.py --tenant acme
```

`deploy.sh` does the following:

1. Refuses the wrong account.
2. Bootstraps the account: GitHub OIDC trust for **your** `origin` repo, `serve7-deploy`/`serve7-plan` roles,
   permissions boundary, state bucket and CloudTrail. Set `CREATE_TRAIL=false` if an org trail exists.
3. Runs pre-flight.
4. Applies the layers 10 → 20 → 40 → 30 → 50.

Expect about 10 minutes. On the first create, layer 40 is applied twice; that is normal.

The admin user's initial password is in Secrets Manager `s7-<tenant>-<env>/connect-user/<user>`. A person opens it
there; it never goes into chat.

## 8. Turn on CI (person)

1. With environments: `gh api -X PUT repos/<org>/<repo>/environments/acme-dev`. For prod, add required reviewers.
2. Set `AWS_CI=true`.
3. Actions → **deploy** → Run workflow (tenant empty = `DEFAULT_TENANT`). It must end green, including the smoke tests.
4. `python3 scripts/org-setup.py check --aws` should show no `[todo]`.

From now on:

- a PR shows a Terraform plan comment;
- merging to `main` deploys the tenants in `DEFAULT_TENANT` or `CI_TENANTS` (never prod);
- prod goes only through a manual run with approval.

## 9. Optional

- **AI agent with MCP tools:** set `enable_agentic_tools = true` in `dev.tfvars`. The bootstrap already allows the Connect
  service-linked-role change that the MCP integration needs.
- **Persona:** after layer 40 is deployed once, run `python3 scripts/fetch-orchestration-prompt.py --tenant acme` and
  commit the file it writes. Then fill `persona` in `agent.yaml`.
- **Production:** see "Production environment" in [runbook-new-tenant.md](runbook-new-tenant.md). Use SAML, a separate
  account and required reviewers.

## The two CI trusts

| | Accelerator (tenants) | Demo kit (`demos/`) |
|---|---|---|
| Created by | `scripts/deploy.sh` → stack `serve7-bootstrap` | `demos/ccg-s5/ci/setup-github-trust.sh` → stack with role `serve7-ccg-s5-ci` |
| Roles | `serve7-deploy` (environment or main branch), `serve7-plan` (PRs) | one CI role, `main` branch only |
| Repo it trusts | from the `origin` remote at first bootstrap; change with `RETRUST=1` | `GitHubOrg`/`GitHubRepo`/`GitHubRepoId` in `demos/ccg-s5/ci/github-trust.yaml` |
| Workflows | `plan.yml`, `deploy.yml` | `demo.yml`, `ccg-s5.yml`, `auto-ship.yml` |

You only need the second trust if you use the demos. They can share the account with a tenant, or use a separate one.

## Moving an account to a new repo or org

If the repo was transferred or copied and the account was bootstrapped for the old one, run this from a git clone of the
new repo, in that account, with admin credentials:

```bash
RETRUST=1 ./scripts/deploy.sh <tenant> <env>
```

It re-reads the GitHub org and repo from `origin`, and the OIDC subject prefix through `gh`. If `gh` cannot read the
prefix, it clears it. If your org uses customised OIDC subjects, pass `GITHUB_SUBJECT_PREFIX=...`.
`org-setup.py check --aws` detects this case.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `deploy.sh`: "cannot tell which GitHub repo CI runs from" | Clone with git, or `GITHUB_ORG=<org> GITHUB_REPO=<repo> ./scripts/deploy.sh ...` |
| CI: "Not authorized to perform sts:AssumeRoleWithWebIdentity" | Trust mismatch: `org-setup.py check --aws`; `RETRUST=1`, or `USE_ENVIRONMENTS` / `DEPLOY_TRUST` disagree |
| Plan or deploy runs for `sandbox` in your copy | Workflows not installed from `ci/workflows/` (step 2), or `DEFAULT_TENANT` not set |
| "account ... is bootstrapped for environment 'dev', not 'prod'" | One environment per account: use a separate account for prod |
| Bedrock "account under verification" / no model | `SKIP_MODEL_TEST=1`, keep `bedrock_model_id: amazon.nova-lite`, retry later |
| Phone number claim fails | Quota 0 on new accounts: `claim_phone_number = false` until the increase is granted |
| Stack `UPDATE_ROLLBACK_COMPLETE` after a tag change | Fixed in code (`ignore_changes` on stack tags); the next successful deploy clears the status |
