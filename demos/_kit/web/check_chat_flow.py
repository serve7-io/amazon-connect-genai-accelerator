#!/usr/bin/env python3
"""Ask Connect why a demo's chat flow is rejected (CloudFormation only shows 'InvalidContactFlowException').

    python3 demos/_kit/web/check_chat_flow.py <demo> [--dry-run]

Renders the demo's ChatFlow exactly as CloudFormation would (template + demo.json web block + the deployment key from
Secrets Manager demo-kit/web/<demo>/acxd), then creates a throwaway flow "zz-check-<demo>" in the Connect instance and
prints Connect's own problem list. A flow that gets created is deleted again. The deployment key is never printed
(only its length and whether it contains spaces or quotes). --dry-run renders and checks the flow graph offline.
"""
import json
import re
import sys
import textwrap
from pathlib import Path

DEMOS = Path(__file__).resolve().parents[2]


def render(demo, key):
    d = DEMOS / demo
    cfg = json.loads((d / "demo.json").read_text(encoding="utf-8"))
    web = cfg["web"]
    text = (d / "template.yaml").read_text(encoding="utf-8")
    m = re.search(r"Content: !Sub \|\n(.*?)\n\nOutputs:", text, re.S)
    if not m:
        sys.exit("could not find the ChatFlow content in template.yaml")
    flow = textwrap.dedent(m.group(1))
    values = {"AcxdWorkspaceId": web["acxd"]["workspace_id"], "AcxdApplicationId": web["acxd"]["application_id"], "AcxdAlias": key}
    flow = re.sub(r"\$\{(\w+)\}", lambda mm: values[mm.group(1)], flow)
    doc = json.loads(flow)
    ids = {a["Identifier"] for a in doc["Actions"]}
    for a in doc["Actions"]:
        tr = a["Transitions"]
        targets = {tr.get("NextAction")} | {c["NextAction"] for c in tr.get("Conditions", [])} | {e["NextAction"] for e in tr.get("Errors", [])}
        missing = targets - {None} - ids
        if missing:
            sys.exit(f"action {a['Identifier']} points at unknown action(s) {missing}")
    return cfg, web, flow


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if len(args) != 1:
        sys.exit(__doc__)
    demo = args[0]
    key = "DRY-RUN-KEY"
    if not dry:
        import boto3
        sm = boto3.client("secretsmanager", region_name=json.loads((DEMOS / demo / "demo.json").read_text())["region"])
        secret = json.loads(sm.get_secret_value(SecretId=f"demo-kit/web/{demo}/acxd")["SecretString"])
        key = secret.get("deploymentKey", "")
        odd = any(c in key for c in " \"'\n\r\t")
        print("deployment key in the secret: length", len(key), "| has space/quote/newline:", odd,
              "| apiKey stored:", bool(secret.get("apiKey")))
        if not key:
            sys.exit("the secret has no deploymentKey")
    cfg, web, flow = render(demo, key)
    print(f"flow rendered OK ({len(flow)} chars, {len(json.loads(flow)['Actions'])} actions); instance {web['connect_instance_id']}, "
          f"workspace {web['acxd']['workspace_id']}, application {web['acxd']['application_id']}")
    if dry:
        print("dry run: nothing sent to Connect")
        return
    connect = boto3.client("connect", region_name=cfg["region"])
    name = f"zz-check-{demo}"
    try:
        r = connect.create_contact_flow(InstanceId=web["connect_instance_id"], Name=name, Type="CONTACT_FLOW", Content=flow)
    except Exception as e:  # noqa: BLE001 - we want Connect's whole answer
        resp = getattr(e, "response", {}) or {}
        print("CONNECT REJECTED THE FLOW")
        print(" error  :", resp.get("Error", {}))
        for k, v in resp.items():
            if k not in ("Error", "ResponseMetadata"):
                print(f" {k}:", json.dumps(v))
        if not resp:
            print(" ", repr(e))
        sys.exit(1)
    print("Connect ACCEPTED the flow, so the flow itself is valid. Deleting the throwaway copy.")
    connect.delete_contact_flow(InstanceId=web["connect_instance_id"], ContactFlowId=r["ContactFlowId"])
    print("deleted", r["ContactFlowId"])


if __name__ == "__main__":
    main()
