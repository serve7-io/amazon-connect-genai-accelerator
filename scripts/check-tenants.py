#!/usr/bin/env python3
"""CI gate: every tenants/<name>/ folder is complete and consistent (runs in validate.yml).

Checks: required files, tenant name matches folder, 12-digit unique account ids, unique instance aliases,
tfvars agree with tenant.yaml, routing.yaml references valid hours/queues, no leftover template placeholders.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ["tenant.yaml", "dev.tfvars", "routing.yaml"]
errors: list[str] = []


def err(tenant: str, msg: str) -> None:
    errors.append(f"{tenant}: {msg}")


def tfvar(text: str, key: str) -> str | None:
    m = re.search(rf'^\s*{key}\s*=\s*"([^"]*)"', text, re.M)
    return m.group(1) if m else None


accounts: dict[str, str] = {}
aliases: dict[str, str] = {}
tenants = sorted(p for p in (ROOT / "tenants").iterdir() if p.is_dir())
for d in tenants:
    name = d.name
    missing = [f for f in REQUIRED if not (d / f).exists()]
    if missing:
        err(name, f"missing {missing}")
        continue
    for f in d.rglob("*"):
        if f.is_file() and "{{" in f.read_text(errors="ignore"):
            err(name, f"{f.relative_to(d)} still has template placeholders")
    t = yaml.safe_load((d / "tenant.yaml").read_text())
    if t.get("tenant") != name:
        err(name, f"tenant.yaml says tenant={t.get('tenant')!r}, folder is {name!r}")
    acct = str(t.get("account_id", ""))
    if not re.fullmatch(r"\d{12}", acct):
        err(name, f"account_id {acct!r} is not 12 digits (quote it in YAML)")
    elif acct in accounts:
        err(name, f"account {acct} also used by {accounts[acct]} (one Connect tenant per account)")
    accounts[acct] = name
    alias = t.get("instance_alias")
    if alias:
        if alias in aliases:
            err(name, f"instance_alias {alias} also used by {aliases[alias]}")
        aliases[alias] = name

    for tf in d.glob("*.tfvars"):
        text = tf.read_text()
        for key, want in (("tenant", name), ("region", t.get("region")), ("instance_alias", alias)):
            got = tfvar(text, key)
            if got is not None and want and got != want:
                err(name, f"{tf.name}: {key}={got!r} but tenant.yaml has {want!r}")
        rf = tfvar(text, "routing_file")
        if rf and not (ROOT / "stacks" / "30-routing" / rf).resolve().exists():
            err(name, f"{tf.name}: routing_file {rf} does not exist")

    r = yaml.safe_load((d / "routing.yaml").read_text())
    hours = set((r.get("hours") or {}).keys())
    queues = r.get("queues") or {}
    if "business" not in hours:
        err(name, "routing.yaml needs a 'business' hours entry (used by the inbound flow)")
    for q in ("general", "billing"):
        if q not in queues:
            err(name, f"routing.yaml needs queue '{q}' (used by the inbound flow)")
    for qn, q in queues.items():
        if q.get("hours") not in hours:
            err(name, f"queue {qn} uses unknown hours {q.get('hours')!r}")
    for pn, p in (r.get("routing_profiles") or {}).items():
        for q in p.get("queues", []):
            if q["name"] not in queues:
                err(name, f"routing profile {pn} references unknown queue {q['name']!r}")
    for m in ("ai_disclosure", "closed", "failure"):
        if not (r.get("messages") or {}).get(m):
            err(name, f"routing.yaml messages.{m} is required")

print(f"checked {len(tenants)} tenant(s): {', '.join(p.name for p in tenants)}")
if errors:
    print("\n".join(f"  FAIL {e}" for e in errors))
    sys.exit(1)
print("  all tenant folders valid")
