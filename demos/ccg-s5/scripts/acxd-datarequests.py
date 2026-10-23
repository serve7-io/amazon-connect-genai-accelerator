#!/usr/bin/env python3
"""Write the 6 ACXD data request files for THIS account's deployment (CloudShell, after the stack is up).

    python3 ccg-s5/scripts/acxd-datarequests.py --out ~/ccg-s5-acxd

The repo copies in acxd/datarequests/ are templates. This replaces their URL with the stack's AcxdApiUrl output.
The x-api-key header references the ACXD workspace secret {ccg_s5_api_key:NLX.Secret}: create that secret once in
ACXD (Resources > Secrets) with the value of Secrets Manager secret ccg-s5/acxd-api-key. The key is never in a file.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

import boto3

ROOT = Path(__file__).resolve().parents[1]
SECRET_REF = "{ccg_s5_api_key:NLX.Secret}"

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--stack", default="ccg-s5")
a = ap.parse_args()
outputs = {o["OutputKey"]: o["OutputValue"] for o in
           boto3.client("cloudformation").describe_stacks(StackName=a.stack)["Stacks"][0]["Outputs"]}
base = outputs["AcxdApiUrl"].rstrip("/")
out = Path(a.out).expanduser()
out.mkdir(parents=True, exist_ok=True)
for src in sorted((ROOT / "acxd" / "datarequests").glob("*.datarequest.json")):
    d = json.loads(src.read_text())
    for env in d["webhook"]["environments"].values():
        env["url"] = base + urlparse(env["url"]).path
        for h in env.get("headers", []):
            if h["key"].lower() == "x-api-key":
                h["value"] = SECRET_REF
    (out / src.name).write_text(json.dumps(d, indent=2) + "\n")
    print(f"{src.name}: {env['url']}")
print(f"Wrote {out}. Import each file in ACXD (data request > Settings > Import, then Save); the API key comes from "
      f"the ACXD secret ccg_s5_api_key.")
