#!/usr/bin/env python3
"""Seed the demo CRM table from tenants/<tenant>/demo-crm.json (fictional customers, sandbox/demo tenants only).

    python3 scripts/seed-demo-crm.py --tenant sandbox [--env dev]

Idempotent: rows that already exist are left untouched (tickets created by the AI agent are never overwritten).
Runs from deploy.sh after layer 40 with the deploy role; deliberately outside Terraform so the read-only plan role
never needs to decrypt table data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]


def rows(customers: list[dict]) -> list[dict]:
    out = []
    for c in customers:
        cid = c["customer_id"]
        out.append({"pk": f"CUST#{cid}", "sk": "PROFILE", "first_name": c["first_name"], "last_name": c["last_name"],
                    "tier": c["tier"], "phone": c["phone"], "email": c["email"]})
        out.append({"pk": f"PHONE#{c['phone']}", "sk": "ALIAS", "customer_id": cid})
        out.append({"pk": f"EMAIL#{c['email'].lower()}", "sk": "ALIAS", "customer_id": cid})
        for o in c.get("orders", []):
            out.append({"pk": f"CUST#{cid}", "sk": f"ORDER#{o['order_id']}", "item": o["item"],
                        "status": o["status"], "eta": o["eta"]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--env", default="dev")
    a = ap.parse_args()
    seed = ROOT / "tenants" / a.tenant / "demo-crm.json"
    if not seed.exists():
        print(f"no {seed.relative_to(ROOT)} - nothing to seed")
        return
    table_name = f"s7-{a.tenant}-{a.env}-demo-crm"
    table = boto3.resource("dynamodb").Table(table_name)
    try:
        table.load()
    except ClientError as err:
        if err.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"{table_name} not deployed (enable_agentic_tools = false) - skipping")
            return
        raise
    added = kept = 0
    for item in rows(json.loads(seed.read_text())["customers"]):
        try:
            table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
            added += 1
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            kept += 1
    print(f"{table_name}: {added} rows added, {kept} already present")


if __name__ == "__main__":
    try:
        main()
    except ClientError as err:
        sys.exit(f"seed failed: {err.response['Error']['Code']}")
