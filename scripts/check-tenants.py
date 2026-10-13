#!/usr/bin/env python3
"""CI gate: every tenants/<name>/ folder is complete and consistent (runs in validate.yml).

Checks: required files, tenant name matches folder, 12-digit unique account ids, unique instance aliases,
tfvars agree with tenant.yaml, routing.yaml references valid hours/queues, no leftover template placeholders.
"""
from __future__ import annotations

import json
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
    envs = {t.get("environment", "dev"): {"account_id": t.get("account_id"), "instance_alias": t.get("instance_alias")}}
    for en, ov in (t.get("environments") or {}).items():
        envs[en] = {"account_id": ov.get("account_id", t.get("account_id")), "instance_alias": ov.get("instance_alias")}
    for en, e in envs.items():
        acct = str(e["account_id"] or "")
        if not re.fullmatch(r"\d{12}", acct):
            err(name, f"{en}: account_id {acct!r} is not 12 digits (quote it in YAML)")
        elif acct in accounts:
            err(name, f"{en}: account {acct} also used by {accounts[acct]} (one environment per account)")
        accounts[acct] = f"{name}/{en}"
        if not e["instance_alias"]:
            err(name, f"{en}: instance_alias missing")
        elif e["instance_alias"] in aliases:
            err(name, f"{en}: instance_alias {e['instance_alias']} also used by {aliases[e['instance_alias']]}")
        else:
            aliases[e["instance_alias"]] = f"{name}/{en}"
        if not (d / f"{en}.tfvars").exists():
            err(name, f"environment {en} has no {en}.tfvars")

    for tf in d.glob("*.tfvars"):
        text = tf.read_text()
        en = tf.stem
        if en not in envs:
            err(name, f"{tf.name} has no matching environment in tenant.yaml")
            continue
        for key, want in (("tenant", name), ("region", t.get("region")), ("instance_alias", envs[en]["instance_alias"]),
                          ("environment", en)):
            got = tfvar(text, key)
            if got is not None and want and got != want:
                err(name, f"{tf.name}: {key}={got!r} but tenant.yaml has {want!r}")
        if en == "prod":
            rules = {
                "enforce_guardrail_iam": (r"^\s*enforce_guardrail_iam\s*=\s*true", "must be true"),
                "kinesis_stream_mode": (r'^\s*kinesis_stream_mode\s*=\s*"ON_DEMAND"', 'must be "ON_DEMAND"'),
            }
            for key, (rx, why) in rules.items():
                if not re.search(rx, text, re.M):
                    err(name, f"prod.tfvars: {key} {why}")
            m = re.search(r"^\s*recording_retention_days\s*=\s*(\d+)", text, re.M)
            if m and int(m.group(1)) < 90:
                err(name, "prod.tfvars: recording_retention_days below 90")
            if re.search(r'identity_management_type\s*=\s*"CONNECT_MANAGED"', text) and "allow_local_users_in_prod" not in text:
                err(name, "prod.tfvars: CONNECT_MANAGED users in prod need an explicit '# allow_local_users_in_prod' waiver")
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

    # optional: AI agent settings (stacks/40-genai/agent.tf) and fictional demo CRM data (seed-demo-crm.py)
    if (d / "agent.yaml").exists():
        ag = yaml.safe_load((d / "agent.yaml").read_text()) or {}
        unknown = set(ag) - {"company", "assistant_name", "persona", "customer_id_example", "ticket_categories"}
        if unknown:
            err(name, f"agent.yaml has unknown keys {sorted(unknown)}")
        persona = str(ag.get("persona") or "")
        if len(persona) > 3000:
            err(name, f"agent.yaml persona is {len(persona)} chars; keep it under 3000")
        cats = ag.get("ticket_categories", [])
        if not isinstance(cats, list) or not all(re.fullmatch(r"[a-z][a-z0-9_-]{1,30}", str(c)) for c in cats):
            err(name, "agent.yaml ticket_categories must be a list of short lowercase words")
    if (d / "demo-crm.json").exists():
        try:
            crm = json.loads((d / "demo-crm.json").read_text())
            first = crm["customers"][0]
            assert first["orders"][0]["order_id"] and first["phone"] and first["customer_id"]
            for c in crm["customers"]:
                if not re.fullmatch(r"\+1\d{3}55501\d{2}", c["phone"]) or not c["email"].endswith("@example.com"):
                    err(name, f"demo-crm.json {c['customer_id']}: use fictional contacts (+1NPA55501xx, @example.com)")
        except (ValueError, KeyError, IndexError, AssertionError, TypeError) as e:
            err(name, f"demo-crm.json needs customers[0] with customer_id, phone and one order ({e!r})")

# ---- optional data-tables.yaml
VALUE_TYPES = {"TEXT", "NUMBER", "BOOLEAN", "TEXT_LIST", "NUMBER_LIST"}
NAME_RE = re.compile(r"^[\w\- .:=@'|]{1,127}$")
for d in tenants:
    f = d / "data-tables.yaml"
    if not f.exists():
        continue
    tables = (yaml.safe_load(f.read_text()) or {}).get("tables") or {}
    if len(tables) > 100:
        err(d.name, "data-tables.yaml: more than 100 tables (Connect default quota)")
    for tn, tbl in tables.items():
        where = f"data-tables.yaml {tn}"
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,60}", tn):
            err(d.name, f"{where}: table names use letters, digits, _ and - (they become CloudFormation stack names)")
        attrs = tbl.get("attributes") or []
        names = [a.get("name", "") for a in attrs]
        if not attrs:
            err(d.name, f"{where}: needs attributes")
        if len({n.lower() for n in names}) != len(names):
            err(d.name, f"{where}: attribute names must be unique (case-insensitive)")
        if len({re.sub(r"[^A-Za-z0-9]", "", n) for n in names}) != len(names):
            err(d.name, f"{where}: attribute names must stay unique without spaces/punctuation (template logical ids)")
        for a in attrs:
            if str(a.get("type", "TEXT")).upper() not in VALUE_TYPES:
                err(d.name, f"{where}: attribute {a.get('name')!r} type must be one of {sorted(VALUE_TYPES)}")
            if not NAME_RE.match(a.get("name", "")):
                err(d.name, f"{where}: invalid attribute name {a.get('name')!r}")
        keys = [a["name"] for a in attrs if a.get("primary")]
        seen = set()
        for i, row in enumerate(tbl.get("rows") or []):
            unknown = set(row) - set(names)
            if unknown:
                err(d.name, f"{where}: row {i + 1} has unknown attributes {sorted(unknown)}")
            missing = [k for k in keys if row.get(k) in (None, "")]
            if missing:
                err(d.name, f"{where}: row {i + 1} is missing primary attributes {missing}")
            pk = tuple(str(row.get(k)) for k in keys)
            if keys and pk in seen:
                err(d.name, f"{where}: row {i + 1} duplicates primary key {pk}")
            seen.add(pk)

print(f"checked {len(tenants)} tenant(s): {', '.join(p.name for p in tenants)}")
if errors:
    print("\n".join(f"  FAIL {e}" for e in errors))
    sys.exit(1)
print("  all tenant folders valid")
