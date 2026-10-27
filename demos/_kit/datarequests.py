#!/usr/bin/env python3
"""Print the demo's ACXD data request URLs for this stack and write ready-to-import copies
($RUNNER_TEMP/<demo>-acxd or ~/<demo>-acxd). The x-api-key header references the ACXD workspace secret named in
demo.json (acxd_secret); no key is in any file."""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import boto3

d = Path(__file__).resolve().parent.parent / sys.argv[1]
cfg = json.loads((d / "demo.json").read_text())
cf = boto3.client("cloudformation", region_name=cfg["region"])
outs = {o["OutputKey"]: o["OutputValue"] for o in cf.describe_stacks(StackName=cfg["stack"])["Stacks"][0]["Outputs"]}
base = outs["AcxdApiUrl"].rstrip("/")
out = Path(os.environ.get("RUNNER_TEMP") or Path.home()) / f"{cfg['name']}-acxd"
out.mkdir(parents=True, exist_ok=True)
print(f"API base URL: {base}")
for src in sorted((d / "acxd" / "datarequests").glob("*.datarequest.json")):
    dr = json.loads(src.read_text())
    for env in dr["webhook"]["environments"].values():
        env["url"] = base + urlparse(env["url"]).path
        for h in env.get("headers", []):
            if h["key"].lower() == "x-api-key":
                h["value"] = "{%s:NLX.Secret}" % cfg["acxd_secret"]
    (out / src.name).write_text(json.dumps(dr, indent=2, ensure_ascii=False) + "\n")
    print(f"  {src.name}: {env['url']}")
