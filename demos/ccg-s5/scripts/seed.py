#!/usr/bin/env python3
"""Load data/synthetic.json into the stack's tables: python3 scripts/seed.py [--prefix ccg-s5] [--reset]"""
import argparse, json
from decimal import Decimal
from pathlib import Path
import boto3

ap = argparse.ArgumentParser(); ap.add_argument("--prefix", default="ccg-s5"); ap.add_argument("--reset", action="store_true")
a = ap.parse_args()
data = json.loads((Path(__file__).resolve().parents[1] / "data" / "synthetic.json").read_text(), parse_float=Decimal)
ddb = boto3.resource("dynamodb")
for table, rows in (("members", data["members"]), ("policies", data["policies"]), ("payment-methods", data["payment_methods"])):
    t = ddb.Table(f"{a.prefix}-{table}")
    with t.batch_writer() as w:
        for r in rows:
            w.put_item(Item=dict(r))  # amounts stay strings ("1284.60"): DynamoDB numbers drop trailing zeros
    print(f"{t.name}: {len(rows)} rows")
if a.reset:  # demo reset: clear payments and notes so policies can be paid again
    for name, keys in ((f"{a.prefix}-payments", ["idempotency_key"]), (f"{a.prefix}-notes", ["policy_id", "created_at"])):
        t = ddb.Table(name)
        for it in t.scan(ProjectionExpression=", ".join(keys))["Items"]:
            t.delete_item(Key={k: it[k] for k in keys})
        print(f"{name}: cleared")
