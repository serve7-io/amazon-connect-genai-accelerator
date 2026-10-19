#!/usr/bin/env python3
"""Demo data operations (run by the demo workflow or in CloudShell).

    python3 demos/_kit/data.py <demo> status
    python3 demos/_kit/data.py <demo> seed            # only fills empty tables
    python3 demos/_kit/data.py <demo> reset           # wipe the tables and load data/seed.json again
    python3 demos/_kit/data.py <demo> events          # read-only: stack status and the latest failed events
    python3 demos/_kit/data.py <demo> <command> ...   # demo-specific, from demos/<demo>/admin.py COMMANDS

Tables are the stack outputs named in data/seed.json ("tables": {"<OutputName>": [rows]}).
Strings like "+3" in date fields (keys ending in _date or end_exclusive) become today + 3 days.
"""
import datetime as dt
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import boto3

KIT = Path(__file__).resolve().parent
DEMOS = KIT.parent


def load(name):
    d = DEMOS / name
    if not (d / "demo.json").is_file():
        sys.exit(f"unknown demo '{name}'")
    return d, json.loads((d / "demo.json").read_text())


def outputs(cfg):
    cf = boto3.client("cloudformation", region_name=cfg["region"])
    st = cf.describe_stacks(StackName=cfg["stack"])["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in st.get("Outputs", [])}


def table(cfg, out_name):
    return boto3.resource("dynamodb", region_name=cfg["region"]).Table(outputs(cfg)[out_name])


def scan(t):
    items, kw = [], {}
    while True:
        p = t.scan(**kw)
        items += p.get("Items", [])
        if "LastEvaluatedKey" not in p:
            return items
        kw["ExclusiveStartKey"] = p["LastEvaluatedKey"]


def _resolve(v, key, today):
    if isinstance(v, str) and (key.endswith("_date") or key == "end_exclusive") and v[:1] in "+-" and v[1:].isdigit():
        return (today + dt.timedelta(days=int(v))).isoformat()
    if isinstance(v, float):
        return Decimal(str(v))
    return v


def seed(d, cfg, reset=False):
    data = json.loads((d / cfg.get("seed", "data/seed.json")).read_text())
    today = dt.date.today()
    for out_name, rows in data["tables"].items():
        t = table(cfg, out_name)
        keys = [k["AttributeName"] for k in t.key_schema]
        existing = scan(t)
        if existing and not reset:
            print(f"{out_name}: {len(existing)} rows already - kept (use reset to reload)")
            continue
        # overwrite_by_pkeys: a seed row that has the same key as an existing row replaces its delete in the batch;
        # DynamoDB rejects a BatchWriteItem that holds two requests for one key
        with t.batch_writer(overwrite_by_pkeys=keys) as bw:
            for it in existing:
                bw.delete_item(Key={k: it[k] for k in keys})
            for r in rows:
                bw.put_item(Item={k: _resolve(v, k, today) for k, v in r.items()})
        print(f"{out_name}: {'reset to' if reset else 'seeded'} {len(rows)} rows")


def status(d, cfg):
    o = outputs(cfg)
    print(f"stack {cfg['stack']} ({cfg['region']}): API {o.get('AcxdApiUrl', '-')}")
    for out_name in cfg.get("status_tables", []):
        rows = scan(table(cfg, out_name))
        print(f"\n{out_name}: {len(rows)} rows")
        for r in sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, default=str))[:60]:
            print("  " + json.dumps({k: v for k, v in r.items() if k not in ("highlights",)}, default=str,
                                    sort_keys=True, ensure_ascii=False))


def events(d, cfg):
    cf = boto3.client("cloudformation", region_name=cfg["region"])
    try:
        st = cf.describe_stacks(StackName=cfg["stack"])["Stacks"][0]
        print(f"stack {cfg['stack']}: {st['StackStatus']} {st.get('StackStatusReason', '')}")
    except Exception as e:  # noqa: BLE001
        print(f"stack {cfg['stack']}: {e}")
        return
    evs = cf.describe_stack_events(StackName=cfg["stack"])["StackEvents"][:60]
    for e in reversed(evs):
        if "FAILED" in e["ResourceStatus"] or e["LogicalResourceId"] == cfg["stack"]:
            print(f"{e['Timestamp']:%H:%M:%S} {e['LogicalResourceId']:<28} {e['ResourceStatus']:<22} "
                  f"{e.get('ResourceStatusReason', '')}")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    d, cfg = load(sys.argv[1])
    cmd, args = sys.argv[2], sys.argv[3:]
    if cmd == "status":
        return status(d, cfg)
    if cmd == "events":
        return events(d, cfg)
    if cmd in ("seed", "reset"):
        return seed(d, cfg, reset=(cmd == "reset"))
    admin = d / "admin.py"
    if admin.is_file():
        spec = importlib.util.spec_from_file_location("admin", admin)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if cmd in mod.COMMANDS:
            return mod.COMMANDS[cmd](lambda n: table(cfg, n), scan, *args)
    sys.exit(f"unknown command '{cmd}'")


if __name__ == "__main__":
    main()
