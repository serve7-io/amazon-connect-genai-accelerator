#!/usr/bin/env python3
"""Resolve per-environment tenant settings from tenants/<tenant>/tenant.yaml.

    python3 scripts/tenant-info.py <tenant> <env> <key>        # prints one value (account_id, region, instance_alias)
    python3 scripts/tenant-info.py <tenant> <env> --github     # key=value lines for $GITHUB_OUTPUT

Top-level keys are the default (dev). An optional block overrides them per environment, e.g.

    environments:
      prod:
        account_id: "222233334444"
        instance_alias: s7-client-prod-334444

One account per environment: state keys and the bootstrap stack are per account, so dev and prod never share one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("account_id", "region", "instance_alias")


def resolve(tenant: str, env: str) -> dict:
    path = ROOT / "tenants" / tenant / "tenant.yaml"
    if not path.exists():
        sys.exit(f"error: {path.relative_to(ROOT)} not found")
    data = yaml.safe_load(path.read_text()) or {}
    out = {k: data.get(k) for k in KEYS}
    override = (data.get("environments") or {}).get(env) or {}
    out.update({k: v for k, v in override.items() if k in KEYS})
    if env != data.get("environment", "dev") and env not in (data.get("environments") or {}):
        sys.exit(f"error: tenant {tenant} has no '{env}' environment (add environments.{env} to tenant.yaml)")
    return {k: ("" if v is None else str(v)) for k, v in out.items()}


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    tenant, env, key = sys.argv[1:]
    info = resolve(tenant, env)
    if key == "--github":
        print("\n".join(f"{k.replace('_id', '')}={v}" for k, v in info.items()))
    elif key in info:
        print(info[key])
    else:
        sys.exit(f"error: unknown key {key}; one of {KEYS}")


if __name__ == "__main__":
    main()
