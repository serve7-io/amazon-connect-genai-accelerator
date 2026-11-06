# Start here: using the accelerator with Claude from your own account

This page is for a colleague who wants to run the accelerator (and optionally the demo kit) from **their own Claude
account, their own GitHub repo and their own AWS account**. It lists everything to set up before the first prompt,
gives prompts you can paste, and explains how to stop paying when you are done.

The detailed technical steps are in [setup-new-org.md](setup-new-org.md). This page gets you and Claude to the point
where you can follow it together.

**Time:** about 30 minutes of setup, then about 1 hour for the first deploy, most of it waiting.

## 1. Checklist before the first prompt

Tick every line. Claude cannot do any of these for you: they need your logins or an admin.

### Repo access

- [ ] **Read access to the source repo** `serve7-io/amazon-connect-genai-accelerator`. It is private: ask a Serve7
      admin to invite your GitHub user (Settings → Collaborators, role *Read*), or to send you a copy.
- [ ] **Your own private repo** in your GitHub org or user, created **empty** (no README), for example
      `<your-org>/connect-accelerator`.
- [ ] **The copy pushed** (on your computer, with git and the GitHub CLI `gh`):

  ```bash
  git clone https://github.com/serve7-io/amazon-connect-genai-accelerator.git connect-accelerator
  cd connect-accelerator
  git remote rename origin serve7                     # keep the source to pull updates later
  git remote add origin https://github.com/<your-org>/connect-accelerator.git
  git push -u origin main
  ```

  If the push is refused for `.github/workflows/...`, your token lacks the workflow scope: run
  `gh auth refresh -s workflow` and push again. The workflows arrive already installed. Do not use a zip download:
  `deploy.sh` needs the git remote to set up the CI trust.

- [ ] **Demo workflows disabled** until you set up the demo kit: GitHub → Actions → `demo`, `ccg-s5` and `auto-ship`
      → "…" → **Disable workflow**. They point at Serve7's AWS role and would only fail.
- [ ] **Repository variable** `AWS_CI` = `false` (Settings → Secrets and variables → Actions → Variables). It keeps CI
      away from AWS until your account is ready.

### Claude

- [ ] **Claude desktop app** with Cowork.
- [ ] **Session linked to your computer**: open the task in the desktop app → "Link to this computer". Claude then
      runs checks in your clone and with your AWS sign-in, without ever seeing a key.
- [ ] **GitHub connector** (Settings → Connectors → GitHub). Authorise it **on your new repo**, so Claude can open
      branches, PRs and issues.
- [ ] **Both skills uploaded.** They are in this repo under [`claude/skills/`](../claude/skills/README.md). Zip each
      folder and upload it in Claude settings → Skills:
  - `connect-accelerator-deployer`: the accelerator (tenants, Terraform layers, CI);
  - `connect-acxd-demo-builder`: the demo kit (ACXD client demos), only if you build demos.
- [ ] **A Claude project** (optional but recommended) for this work. Ask Claude to keep a status note there
      (accounts, tenants, open PRs, temporary console changes), so a new chat can pick up where the last one stopped.
      Project notes are per Claude account: nothing from Serve7's project comes with the repo.

### AWS

- [ ] **An AWS account for the dev tenant** where you are admin. Use a fresh account: Connect allows one instance per
      new account, and the accelerator must not go into a shared or client account.
- [ ] **Root user MFA on**, no root access keys.
- [ ] **A budget alert** (Billing → Budgets → Create budget → monthly cost budget, for example 30 USD, alert to your
      email). Claude may not touch billing; this is your safety net.
- [ ] **Sign-in for the computer**: IAM Identity Center with an admin permission set, and optionally the restricted
      `ClaudeOperator` permission set from [claude-operator-policy.json](claude-operator-policy.json) (setup-new-org.md
      step 4). Then on your computer: `aws configure sso`, `aws sso login`, and restart the Claude app.
- [ ] **Region chosen** with Connect, Q in Connect and Bedrock (for example `ca-central-1` or `us-east-1`).
- [ ] **Phone-number quota increase requested** if you want voice (Service Quotas → Amazon Connect → phone numbers).
      New accounts have 0. Chat works without it.

### Your computer

- [ ] git, Python 3.10+, AWS CLI v2, and `gh` (optional, saves clicks).
- [ ] Windows: do the first deploy in **AWS CloudShell** (clone your repo there).

## 2. Prompts to paste

Replace the `<...>` values. Claude follows the skill and the repo docs, and tells you at every step what you must click
or run yourself.

**First setup**

```text
Use the connect-accelerator-deployer skill. Set up the accelerator from my repo <your-org>/connect-accelerator
in AWS account <12-digit id>, region <region>. Tenant name <tenant>, company "<Company>", owner email <email>,
time zone <Area/City>. GitHub plan: <free private repo | paid with environments>.
Follow docs/start-here-with-claude.md and docs/setup-new-org.md. Start with the readiness check, open a PR for the
tenant scaffold, then give me the exact first-deploy command to run myself.
```

**Readiness check (any time)**

```text
Run python3 scripts/org-setup.py check --aws in my clone and fix or explain every [todo].
```

**Turn on CI after the first deploy**

```text
The first deploy finished. Check the account with org-setup.py, then tell me which GitHub variables and
environments to set to turn CI on, and confirm the deploy run is green afterwards.
```

**Everyday changes** (each one becomes a PR with a Terraform plan; merging deploys dev)

```text
For tenant <tenant>: change opening hours to Mon–Fri 08:00–18:00 <time zone>, closed on weekends.
For tenant <tenant>: add this FAQ to the knowledge base: <text or attached file>.
For tenant <tenant>: turn on the AI agent with MCP tools and explain what it will deploy.
```

**A client demo** (demo kit, needs the second skill)

```text
Use the connect-acxd-demo-builder skill. Set up the demo kit in my account (section 2 of the skill), then build a
demo for <client>: <what the agent should do>. Synthetic data only.
```

**Stop paying**

```text
I am done with the trial. Show me what is still costing money in account <id> and give me the teardown commands
to run myself.
```

## 3. What Claude does, and what stays with you

| Claude does | You do |
|---|---|
| Reads the repo, runs checks on your computer, prepares commands | Logins (`aws sso login`, Connect, ACXD), anything with a password or key |
| Opens branches and PRs, reads CI results, opens demo issues | Merges you have reviewed, workflow files in `.github/workflows/`, repo settings |
| Explains each `[todo]` and the exact fix | The first deploy (`deploy.sh`, about 10 minutes, needs admin) |
| Drives the ACXD and Connect consoles in its browser while you are signed in | ACXD **Build & deploy** clicks, deletions, anything that costs money |

Claude never asks for or shows passwords, access keys or session tokens. If you paste one into the chat by mistake,
revoke or rotate it. Claude asks before deleting anything, before spending money and before anything that touches a
client or production account.

## 4. Costs and teardown

A dev tenant left running costs roughly 25–40 USD a month, mostly two provisioned Kinesis streams (about 11 USD each),
plus KMS keys (1 USD each), a phone number if you claimed one, and Bedrock and Contact Lens usage while you test. A
one-day trial that you tear down the same day costs a few dollars.

To tear down (you run these, with admin credentials in that account):

1. Set the repository variable `AWS_CI` = `false`, so a merge does not rebuild what you delete.
2. Remove the tenant, layer by layer (asks you to type the tenant name):

   ```bash
   ./scripts/destroy.sh <tenant> dev
   ```

3. Check for anything left (demos, buckets that were not empty, streams), first without changing anything:

   ```bash
   bash tools/account-shutdown.sh            # inventory only
   bash tools/account-shutdown.sh --delete   # deletes; asks you to type the account id
   ```

   It only touches project names (`s7-*`, `serve7-*`, `ccg-*`, `demo-*`). It keeps the `serve7-bootstrap` stack (roles,
   state bucket, CloudTrail), which costs close to nothing and lets you redeploy later with one command.

Removing your credit card does **not** stop charges. Delete the resources, or close the account.

## 5. Keeping your copy up to date

Serve7's fixes land in the source repo. To take them:

```bash
git fetch serve7
git checkout -b update-from-serve7 && git merge serve7/main
git push -u origin update-from-serve7      # open a PR, check validate and plan, then merge
```

If `ci/workflows/` changed, copy those files into `.github/workflows/` in the same branch
(`bash scripts/install-workflows.sh --apply`). Updated skills are in `claude/skills/`: re-zip and re-upload them.

## 6. If something goes wrong

- The table in [setup-new-org.md → Troubleshooting](setup-new-org.md#troubleshooting) and section 5 of the
  `connect-accelerator-deployer` skill cover every failure seen so far.
- Ask Claude: "The deploy failed with <paste the error lines, no secrets>. What happened?"
- `python3 scripts/org-setup.py check --aws` is always a safe first step: it only reads.
