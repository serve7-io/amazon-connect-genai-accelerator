#!/usr/bin/env python3
"""Which tenants CI plans and deploys. Used by plan.yml and deploy.yml, so a copy of this repo in another
GitHub org picks its own tenants with repository variables instead of editing the workflows.

    python3 scripts/ci-tenants.py                       # matrix from repository variables (see below)
    python3 scripts/ci-tenants.py --no-prod             # same, never prod (push-triggered deploys)
    python3 scripts/ci-tenants.py --tenant acme --env dev
    python3 scripts/ci-tenants.py --github              # matrix=... and count=... lines for $GITHUB_OUTPUT

Repository variables (Settings -> Secrets and variables -> Actions -> Variables), read from the environment:
    CI_TENANTS      space- or comma-separated "tenant" or "tenant/env" (e.g. "acme/dev beta/dev")
    DEFAULT_TENANT  used when CI_TENANTS is empty (default: sandbox)
    DEFAULT_ENV     environment for entries without "/env" (default: dev)

Entries whose tenant folder or environment does not exist are skipped with a warning, so a fresh copy of the
repo never fails CI because of another organisation's tenant.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,19}$")
ENVS = ("dev", "test", "prod")


def _environments(tenant_dir: Path) -> set[str]:
    """Environments a tenant folder defines: the top-level one plus environments.<env> blocks with a tfvars file."""
    try:
        import yaml
        data = yaml.safe_load((tenant_dir / "tenant.yaml").read_text()) or {}
    except Exception:   # unreadable tenant.yaml: check-tenants.py reports it; CI skips it here
        return set()
    envs = {str(data.get("environment") or "dev")} | set((data.get("environments") or {}).keys())
    return {e for e in envs if (tenant_dir / f"{e}.tfvars").exists()}


def resolve(spec: str, default_tenant: str, default_env: str, no_prod: bool, root: Path = ROOT) -> tuple[list[dict], list[str]]:
    entries = [s for s in re.split(r"[\s,]+", spec.strip()) if s] or [default_tenant]
    out, warnings = [], []
    for e in entries:
        tenant, _, env = e.partition("/")
        env = env or default_env
        if not NAME_RE.match(tenant) or env not in ENVS:
            warnings.append(f"ignored '{e}': expected tenant or tenant/env with env in {ENVS}")
            continue
        if no_prod and env == "prod":
            warnings.append(f"skipped {tenant}/prod: prod is never deployed automatically")
            continue
        tdir = root / "tenants" / tenant
        if not (tdir / "tenant.yaml").exists():
            warnings.append(f"skipped {tenant}/{env}: tenants/{tenant}/tenant.yaml does not exist in this repo")
            continue
        if env not in _environments(tdir):
            warnings.append(f"skipped {tenant}/{env}: no {env} environment (tenant.yaml + {env}.tfvars)")
            continue
        item = {"tenant": tenant, "env": env}
        if item not in out:
            out.append(item)
    return out, warnings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", help="one tenant (manual runs); overrides the variables")
    ap.add_argument("--env", help="environment for --tenant")
    ap.add_argument("--no-prod", action="store_true")
    ap.add_argument("--github", action="store_true", help="print key=value lines for $GITHUB_OUTPUT")
    a = ap.parse_args()

    default_tenant = os.environ.get("DEFAULT_TENANT", "").strip() or "sandbox"
    default_env = os.environ.get("DEFAULT_ENV", "").strip() or "dev"
    if a.tenant:
        spec = f"{a.tenant}/{a.env or default_env}"
    else:
        spec = os.environ.get("CI_TENANTS", "")
    items, warnings = resolve(spec, default_tenant, default_env, a.no_prod)
    for w in warnings:
        print(f"::warning::{w}" if a.github else f"warning: {w}", file=sys.stderr)
    if a.tenant and not items:
        sys.exit(f"error: nothing to run for {spec}")
    matrix = json.dumps({"include": items}, separators=(",", ":"))
    if a.github:
        print(f"matrix={matrix}")
        print(f"count={len(items)}")
    else:
        print(matrix)


if __name__ == "__main__":
    main()
