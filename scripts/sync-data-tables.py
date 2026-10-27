#!/usr/bin/env python3
"""Seed Connect data table rows from tenants/<tenant>/data-tables.yaml through the Data Table Manager Lambda.

    python3 scripts/sync-data-tables.py --tenant sandbox [--env dev] [--overwrite] [--dry-run]

Default is create-only: values that business users changed in Connect are never overwritten. --overwrite makes the
YAML win (upsert). Tables and attributes themselves are deployed by layer 30 (stacks/30-routing/datatables.tf).
Runs from deploy.sh after layer 30.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3
import yaml

ROOT = Path(__file__).resolve().parents[1]


def operations(table: dict) -> list[dict]:
    keys = [a["name"] for a in table["attributes"] if a.get("primary")]
    ops = []
    for row in table.get("rows") or []:
        ops.append({"PrimaryKeyValues": {k: row[k] for k in keys},
                    "AttributeValues": {k: v for k, v in row.items() if k not in keys}})
    return ops


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--env", default="dev")
    ap.add_argument("--overwrite", action="store_true", help="YAML values replace values edited in Connect")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    path = ROOT / "tenants" / a.tenant / "data-tables.yaml"
    if not path.exists():
        print(f"no {path.relative_to(ROOT)} - nothing to seed")
        return
    tables = (yaml.safe_load(path.read_text()) or {}).get("tables") or {}
    fn = f"s7-{a.tenant}-{a.env}-data-table-manager"
    lam = None if a.dry_run else boto3.client("lambda")
    failed = False
    for name, t in tables.items():
        ops = operations(t)
        if not ops:
            continue
        event = {"operation": "UpdateDataTable", "DataTableIdentifier": name, "Operations": ops,
                 "Mode": "upsert" if a.overwrite else "create_only"}
        if a.dry_run:
            print(f"{name}: {len(ops)} row(s) would be sent ({event['Mode']})")
            continue
        out = json.loads(lam.invoke(FunctionName=fn, Payload=json.dumps(event).encode())["Payload"].read())
        if out.get("error") or out.get("Failures"):
            failed = True
            print(f"{name}: FAILED {out.get('error') or out['Failures']}")
        else:
            print(f"{name}: created {out['Created']}, updated {out['Updated']}, unchanged {out['Unchanged']}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
