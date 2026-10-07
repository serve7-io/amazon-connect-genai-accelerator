#!/usr/bin/env python3
"""Scaffold a new tenant folder from templates/tenant.

    python3 scripts/new-tenant.py --tenant client-demo --account-id 123456789012 --region ca-central-1 \
        --company "Client Demo Inc" --owner-email ops@example.com [--timezone America/Toronto] [--country CA]

Creates tenants/<tenant>/{tenant.yaml, dev.tfvars, routing.yaml, knowledge/faq.txt}. Never overwrites.
Next steps are printed at the end (bootstrap + deploy) - see docs/runbook-new-tenant.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "tenant"

TENANT_RE = re.compile(r"^[a-z][a-z0-9-]{1,19}$")          # keeps s7-<tenant>-<env>-* names under AWS limits
ALIAS_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,60}[a-z0-9])?$")
REGIONS_WITH_CONNECT = {
    "ca-central-1", "us-east-1", "us-west-2", "eu-west-2", "eu-central-1", "ap-southeast-1",
    "ap-southeast-2", "ap-northeast-1", "ap-northeast-2", "af-south-1",
}


def fail(msg: str) -> None:
    sys.exit(f"error: {msg}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", required=True, help="short id, lowercase, e.g. client-demo")
    ap.add_argument("--account-id", required=True)
    ap.add_argument("--region", default="ca-central-1")
    ap.add_argument("--company", required=True)
    ap.add_argument("--owner-email", required=True)
    ap.add_argument("--alias", help="Connect instance alias (default s7-<tenant>-<last 6 of account>)")
    ap.add_argument("--timezone", default="America/Toronto")
    ap.add_argument("--country", default="CA")
    ap.add_argument("--cost-center", default="CC-SERVE7-DELIVERY")
    ap.add_argument("--industry", default="unspecified")
    ap.add_argument("--prod-account-id", help="separate production account; also generates prod.tfvars")
    a = ap.parse_args()

    if not TENANT_RE.match(a.tenant):
        fail("--tenant must be 2-20 chars: lowercase letters, digits, hyphens, starting with a letter")
    if not re.fullmatch(r"\d{12}", a.account_id):
        fail("--account-id must be 12 digits")
    if a.region not in REGIONS_WITH_CONNECT:
        fail(f"--region {a.region} is not in the supported Connect region list: {sorted(REGIONS_WITH_CONNECT)}")
    if "@" not in a.owner_email:
        fail("--owner-email must be an email address")
    alias = a.alias or f"s7-{a.tenant}-{a.account_id[-6:]}"
    prod_alias = ""
    if a.prod_account_id:
        if not re.fullmatch(r"\d{12}", a.prod_account_id) or a.prod_account_id == a.account_id:
            fail("--prod-account-id must be 12 digits and differ from --account-id (one environment per account)")
        prod_alias = f"s7-{a.tenant}-prod-{a.prod_account_id[-6:]}"
    if not ALIAS_RE.match(alias):
        fail(f"alias '{alias}' must be lowercase letters, digits and hyphens (max 62)")

    dest = ROOT / "tenants" / a.tenant
    if dest.exists():
        fail(f"{dest.relative_to(ROOT)} already exists - refusing to overwrite")
    for t in (ROOT / "tenants").glob("*/tenant.yaml"):
        for acct in filter(None, (a.account_id, a.prod_account_id)):
            if f'"{acct}"' in t.read_text():
                fail(f"account {acct} is already used by {t.parent.name} (one Connect tenant per account)")

    values = {
        "TENANT": a.tenant, "ACCOUNT_ID": a.account_id, "REGION": a.region, "ALIAS": alias,
        "COMPANY": a.company, "COMPANY_SHORT": a.company.split()[0][:30], "OWNER_EMAIL": a.owner_email,
        "TIMEZONE": a.timezone, "COUNTRY": a.country.upper(), "COST_CENTER": a.cost_center,
        "INDUSTRY": a.industry, "DATE": dt.date.today().isoformat(),
        "PROD_ACCOUNT_ID": a.prod_account_id or "", "PROD_ALIAS": prod_alias,
    }
    written = []
    for src in sorted(TEMPLATE.rglob("*")):
        if src.is_dir():
            continue
        if src.name.endswith(".optional"):
            if not a.prod_account_id:
                continue
            target_name = src.name[: -len(".optional")]
        else:
            target_name = src.name
        text = src.read_text()
        for k, v in values.items():
            text = text.replace("{{" + k + "}}", v)
        leftover = re.findall(r"\{\{[A-Z_]+\}\}", text)
        if leftover:
            fail(f"template {src.name} has unknown placeholders {leftover}")
        out = dest / src.relative_to(TEMPLATE).with_name(target_name)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        written.append(out.relative_to(ROOT))
    if a.prod_account_id:
        ty = dest / "tenant.yaml"
        ty.write_text(ty.read_text() + f'''
# Production lives in its own account (one environment per account).
environments:
  prod:
    account_id: "{a.prod_account_id}"
    instance_alias: {prod_alias}
''')

    print(f"Created tenant '{a.tenant}' ({a.company}) for account {a.account_id} in {a.region}:")
    for w in written:
        print(f"  {w}")
    print(f"""
Next (see docs/runbook-new-tenant.md):
  1. Review tenants/{a.tenant}/routing.yaml (hours, queues, messages) and replace knowledge/faq.txt.
  2. Get credentials for account {a.account_id} (e.g. eval "$(scripts/assume-tenant.sh {a.account_id})").
  3. GITHUB_SUBJECT_PREFIX=... AUTO_APPROVE=1 ./scripts/deploy.sh {a.tenant} dev
  4. python3 tests/smoke.py --tenant {a.tenant}
  5. gh api -X PUT repos/<owner>/<repo>/environments/{a.tenant}-dev   # enables CI deploys for this tenant""")


if __name__ == "__main__":
    main()
