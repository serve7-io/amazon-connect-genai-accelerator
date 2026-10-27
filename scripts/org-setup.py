#!/usr/bin/env python3
"""Readiness check and GitHub setup for running this accelerator from another GitHub org / AWS account.
Read-only unless --apply is given. Designed to be run by a person or an AI assistant; see docs/setup-new-org.md.

    python3 scripts/org-setup.py check                  # repo: remote, tenants, workflows, GitHub variables (needs gh)
    python3 scripts/org-setup.py check --aws            # + AWS: caller account, bootstrap stack and its GitHub trust,
                                                        #   failed stacks, Connect instances (uses current credentials)
    python3 scripts/org-setup.py check --json           # machine-readable
    python3 scripts/org-setup.py github --tenant acme [--env dev] [--no-environments]          # print gh commands
    python3 scripts/org-setup.py github --tenant acme --apply                                  # run them (gh, admin)

Exit code: 0 when nothing is blocking, 1 when a [todo] item blocks a deploy.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ("deploy.yml", "plan.yml", "validate.yml")
# Accounts of the original Serve7 repo. In a copy owned by another org these tenants cannot be deployed from CI.
SERVE7_ACCOUNTS = {"872376836480": "Serve7 sandbox", "915626646645": "Serve7 client-demo", "978387602613": "Serve7"}
PLACEHOLDER_ACCOUNTS = {"000000000000", "000000000001"}


class Report:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, level: str, area: str, msg: str, fix: str = "") -> None:
        self.items.append({"level": level, "area": area, "message": msg, "fix": fix})

    @property
    def blocking(self) -> bool:
        return any(i["level"] == "todo" for i in self.items)

    def print(self) -> None:
        for i in self.items:
            print(f"  [{i['level']:<4}] {i['area']}: {i['message']}")
            if i["fix"]:
                print(f"         -> {i['fix']}")


# ------------------------------------------------------------------ repo
def parse_remote(url: str) -> tuple[str, str] | None:
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    return (m.group(1), m.group(2)) if m else None


def git_remote(root: Path = ROOT) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def load_tenants(root: Path = ROOT) -> dict[str, dict]:
    import yaml
    out = {}
    for t in sorted((root / "tenants").glob("*/tenant.yaml")):
        data = yaml.safe_load(t.read_text()) or {}
        envs = {str(data.get("environment") or "dev"): str(data.get("account_id") or "")}
        for env, block in (data.get("environments") or {}).items():
            envs[env] = str((block or {}).get("account_id") or "")
        out[t.parent.name] = {"region": data.get("region", ""), "environments": envs}
    return out


def workflow_drift(root: Path = ROOT) -> list[str]:
    drift = []
    for name in WORKFLOWS:
        src, dst = root / "ci" / "workflows" / name, root / ".github" / "workflows" / name
        if src.exists() and (not dst.exists() or src.read_bytes() != dst.read_bytes()):
            drift.append(name)
    return drift


def gh_variables(org: str, repo: str) -> dict[str, str] | None:
    if not shutil.which("gh"):
        return None
    try:
        r = subprocess.run(["gh", "variable", "list", "--repo", f"{org}/{repo}", "--json", "name,value"],
                           capture_output=True, text=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    return {v["name"]: v["value"] for v in json.loads(r.stdout or "[]")}


def check_repo(rep: Report, root: Path = ROOT, remote: str | None = None, variables: dict | None | bool = False) -> dict:
    remote = git_remote(root) if remote is None else remote
    parsed = parse_remote(remote)
    ctx: dict = {"org": None, "repo": None}
    if not parsed:
        rep.add("todo", "repo", "no github.com 'origin' remote (zip upload?)",
                "clone the repo with git, or pass GITHUB_ORG/GITHUB_REPO to deploy.sh on the first bootstrap")
    else:
        ctx["org"], ctx["repo"] = parsed
        rep.add("ok", "repo", f"origin is {parsed[0]}/{parsed[1]}")
    is_serve7 = ctx["org"] == "serve7-io"

    tenants = load_tenants(root)
    ctx["tenants"] = tenants
    for name, t in tenants.items():
        for env, acct in t["environments"].items():
            if acct in PLACEHOLDER_ACCOUNTS:
                rep.add("info", "tenants", f"{name}/{env}: placeholder account {acct} (prepare-only tenant)")
            elif acct in SERVE7_ACCOUNTS and not is_serve7:
                rep.add("info", "tenants", f"{name}/{env}: account {acct} belongs to {SERVE7_ACCOUNTS[acct]}; not yours",
                        "keep it out of CI (CI_TENANTS / DEFAULT_TENANT) or delete the folder in your copy")

    if variables is False:
        variables = gh_variables(ctx["org"], ctx["repo"]) if parsed else None
    ctx["variables"] = variables
    default = None
    if variables is None:
        rep.add("warn", "github", "could not read repository variables (gh missing or not logged in)",
                "set DEFAULT_TENANT (and USE_ENVIRONMENTS / AWS_CI) in Settings -> Secrets and variables -> Actions")
    else:
        default = (variables.get("CI_TENANTS") or variables.get("DEFAULT_TENANT") or "sandbox").split()[0].split(",")[0]
        default = default.split("/")[0]
        rep.add("ok", "github", "variables: " + (", ".join(f"{k}={v}" for k, v in sorted(variables.items())) or "none"))
    if default is None:
        default = "sandbox"
    ctx["ci_tenant"] = default
    if default not in tenants:
        rep.add("todo", "ci", f"CI tenant '{default}' has no folder in tenants/",
                "scaffold it with scripts/new-tenant.py, or set DEFAULT_TENANT / CI_TENANTS to an existing tenant")
    else:
        accts = tenants[default]["environments"]
        foreign = [a for a in accts.values() if a in SERVE7_ACCOUNTS and not is_serve7]
        if foreign:
            rep.add("todo", "ci", f"CI deploys tenant '{default}', which points at Serve7's account {foreign[0]}",
                    "set the repository variable DEFAULT_TENANT=<your tenant> (org-setup.py github --tenant ...)")
        else:
            rep.add("ok", "ci", f"CI tenant is '{default}' ({', '.join(f'{e}:{a}' for e, a in accts.items())})")

    drift = workflow_drift(root)
    if drift:
        rep.add("warn", "workflows", f".github/workflows differs from ci/workflows: {', '.join(drift)}",
                "a person installs them: scripts/install-workflows.sh --apply, or paste in the GitHub web editor")
    else:
        rep.add("ok", "workflows", "installed workflows match ci/workflows")
    return ctx


# ------------------------------------------------------------------ AWS
def bootstrap_trust_issues(params: dict[str, str], org: str | None, repo: str | None,
                           use_environments: bool) -> list[tuple[str, str]]:
    """Compare the serve7-bootstrap stack parameters with the repo CI runs from. Returns (message, fix) pairs."""
    issues = []
    if org and repo and (params.get("GitHubOrg"), params.get("GitHubRepo")) != (org, repo):
        issues.append((f"bootstrap trusts {params.get('GitHubOrg')}/{params.get('GitHubRepo')}, but this repo is {org}/{repo}",
                       "RETRUST=1 ./scripts/deploy.sh <tenant> <env>   (admin credentials in that account)"))
    prefix = params.get("GitHubSubjectPrefix", "")
    if prefix and org and f"{org}@" not in prefix and f":{org}/" not in prefix:
        issues.append((f"bootstrap OIDC subject prefix '{prefix}' names another owner",
                       "RETRUST=1 ./scripts/deploy.sh <tenant> <env>   (clears or re-reads the prefix)"))
    trust = params.get("DeployTrust", "environment")
    if trust == "environment" and not use_environments:
        issues.append(("deploy role trusts GitHub environments, but USE_ENVIRONMENTS=false",
                       "DEPLOY_TRUST=main-branch REBOOTSTRAP=1 ./scripts/deploy.sh <tenant> <env>, or create the environment"))
    if trust == "main-branch" and use_environments:
        issues.append(("deploy role trusts main-branch runs, but deploy.yml runs in a GitHub environment",
                       "set the repository variable USE_ENVIRONMENTS=false (or rebootstrap with DEPLOY_TRUST=environment)"))
    return issues


def check_aws(rep: Report, ctx: dict) -> None:
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        rep.add("warn", "aws", "boto3 not installed; skipped AWS checks", "pip install boto3")
        return
    try:
        account = boto3.client("sts").get_caller_identity()["Account"]
    except Exception as e:   # no or expired credentials
        rep.add("todo", "aws", f"no working AWS credentials ({type(e).__name__})", "aws sso login (or aws login), then retry")
        return
    mine = [(t, env) for t, d in ctx["tenants"].items() for env, a in d["environments"].items() if a == account]
    if not mine:
        rep.add("todo", "aws", f"credentials are for {account}, which no tenant uses",
                f"python3 scripts/new-tenant.py --tenant <name> --account-id {account} ...")
        return
    tenant, env = mine[0]
    region = ctx["tenants"][tenant]["region"] or "ca-central-1"
    rep.add("ok", "aws", f"account {account} = tenant {tenant}/{env} ({region})")
    cfn = boto3.client("cloudformation", region_name=region)
    try:
        st = cfn.describe_stacks(StackName="serve7-bootstrap")["Stacks"][0]
    except ClientError:
        rep.add("todo", "aws", "account not bootstrapped (no serve7-bootstrap stack)",
                f"AUTO_APPROVE=1 ./scripts/deploy.sh {tenant} {env}   (admin credentials, from a git clone)")
        return
    params = {p["ParameterKey"]: p.get("ParameterValue", "") for p in st.get("Parameters", [])}
    rep.add("ok" if st["StackStatus"].endswith("_COMPLETE") and "ROLLBACK" not in st["StackStatus"] else "warn",
            "aws", f"serve7-bootstrap {st['StackStatus']} (trust {params.get('GitHubOrg')}/{params.get('GitHubRepo')}, "
                   f"{params.get('DeployTrust', 'environment')})")
    use_env = (ctx.get("variables") or {}).get("USE_ENVIRONMENTS", "true") != "false"
    for msg, fix in bootstrap_trust_issues(params, ctx.get("org"), ctx.get("repo"), use_env):
        rep.add("todo", "trust", msg, fix)
    bad = []
    try:
        for page in cfn.get_paginator("list_stacks").paginate(
                StackStatusFilter=["CREATE_FAILED", "ROLLBACK_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
                                   "UPDATE_ROLLBACK_FAILED", "DELETE_FAILED"]):
            bad += [s["StackName"] for s in page["StackSummaries"] if s["StackName"].startswith(("s7-", "serve7-"))]
    except (BotoCoreError, ClientError):
        pass
    if bad:
        rep.add("warn", "aws", f"stacks in a failed/rolled-back state: {', '.join(bad)}",
                "a successful deploy usually clears UPDATE_ROLLBACK_COMPLETE; see the stack events for others")
    try:
        inst = boto3.client("connect", region_name=region).list_instances()["InstanceSummaryList"]
        rep.add("ok", "connect", f"{len(inst)} Connect instance(s): " + ", ".join(i.get("InstanceAlias", "?") for i in inst)
                if inst else "no Connect instance yet (layer 20 creates one; new accounts allow 1)")
    except (BotoCoreError, ClientError) as e:
        rep.add("warn", "connect", f"cannot list instances ({type(e).__name__})")


# ------------------------------------------------------------------ GitHub setup
def github_commands(repo: str, tenant: str, env: str, environments: bool, aws_ready: bool) -> list[list[str]]:
    cmds = [["gh", "variable", "set", "DEFAULT_TENANT", "--repo", repo, "--body", tenant],
            ["gh", "variable", "set", "DEFAULT_ENV", "--repo", repo, "--body", env],
            ["gh", "variable", "set", "USE_ENVIRONMENTS", "--repo", repo, "--body", "true" if environments else "false"],
            ["gh", "variable", "set", "AWS_CI", "--repo", repo, "--body", "true" if aws_ready else "false"]]
    if environments:
        cmds.append(["gh", "api", "-X", "PUT", f"repos/{repo}/environments/{tenant}-{env}"])
    return cmds


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--aws", action="store_true")
    c.add_argument("--json", action="store_true")
    g = sub.add_parser("github")
    g.add_argument("--tenant", required=True)
    g.add_argument("--env", default="dev")
    g.add_argument("--repo", help="owner/name (default: from the origin remote)")
    g.add_argument("--no-environments", action="store_true",
                   help="free private repo: no GitHub environments (bootstrap with DEPLOY_TRUST=main-branch)")
    g.add_argument("--not-bootstrapped", action="store_true", help="set AWS_CI=false until the first deploy.sh run")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    if a.cmd == "check":
        rep = Report()
        ctx = check_repo(rep)
        if a.aws:
            check_aws(rep, ctx)
        if a.json:
            print(json.dumps({"blocking": rep.blocking, "items": rep.items}, indent=2))
        else:
            print("Accelerator readiness" + (" (repo + AWS)" if a.aws else " (repo; add --aws for the account)"))
            rep.print()
            print("\nBlocking items: " + ("yes" if rep.blocking else "none"))
        sys.exit(1 if rep.blocking else 0)

    repo = a.repo or "/".join(parse_remote(git_remote()) or ())
    if not repo:
        sys.exit("error: no origin remote; pass --repo owner/name")
    if not (ROOT / "tenants" / a.tenant / "tenant.yaml").exists():
        sys.exit(f"error: tenants/{a.tenant}/tenant.yaml does not exist; scaffold it with scripts/new-tenant.py first")
    cmds = github_commands(repo, a.tenant, a.env, not a.no_environments, not a.not_bootstrapped)
    for cmd in cmds:
        print(" ".join(cmd))
        if a.apply:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    if not a.apply:
        print("\n(printed only; run them, or add --apply. Needs gh logged in as a repo admin.)")


if __name__ == "__main__":
    main()
