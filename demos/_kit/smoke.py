#!/usr/bin/env python3
"""Smoke test a kit demo API without ever reading the API key:
   /health answers (code deployed, tables reachable, data present), and a real operation without the key is refused."""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import boto3

d = Path(__file__).resolve().parent.parent / sys.argv[1]
cfg = json.loads((d / "demo.json").read_text())
cf = boto3.client("cloudformation", region_name=cfg["region"])
outs = {o["OutputKey"]: o["OutputValue"] for o in cf.describe_stacks(StackName=cfg["stack"])["Stacks"][0]["Outputs"]}
url = outs["AcxdApiUrl"].rstrip("/")


def call(op, headers=None):
    req = urllib.request.Request(f"{url}/{op}", b"{}", {"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}


fails = 0
code, body = call("health")
ok = code == 200 and body.get("ok") and body.get("units", 0) > 0 and body.get("key_configured")
print(f"  {'PASS' if ok else 'FAIL'}  health ({code}): {body}")
fails += not ok
code, _ = call(cfg["smoke"]["op"], {"x-api-key": "wrong"})
print(f"  {'PASS' if code == 401 else 'FAIL'}  {cfg['smoke']['op']} without the right key is refused ({code})")
fails += code != 401
sys.exit(1 if fails else 0)
