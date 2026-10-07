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

## Removing a tenant

```bash
eval "$(scripts/assume-tenant.sh <account-id>)" && ./scripts/destroy.sh <client> dev
```

Then close the member account in Organizations.

---

## Evidence: client-demo reference deployment

| Step | Started | Finished | Duration | Notes |
|------|---------|----------|----------|-------|
| 0 Account | | | ~1 min | `serve7-client-demo` (915626646645) |
| 1 Scaffold | | | < 1 min | |
| 3 Deploy | | | | |
| 4 Smoke | | | | |
