#!/usr/bin/env python3
"""Ask Amazon Connect to validate the rendered inbound flow BEFORE Terraform applies it.

Terraform drops Connect's problem list from InvalidContactFlowException; this script shows it.
It renders flows/inbound-main.json.tftpl with the tenant's live ARNs, creates a temporary flow
named zz-flowcheck-<tenant>, prints the result, and deletes the temporary flow.

    INSTANCE_ALIAS=serve7-calgary-demo python3 scripts/validate-flow.py --tenant sandbox --env dev
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]


def tenant_value(tenant: str, key: str) -> str:
    for line in (ROOT / "tenants" / tenant / "tenant.yaml").read_text().splitlines():
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].split("#")[0].strip().strip('"')
    return ""


def render(template: str, values: dict) -> str:
    ss = bool(values.get("self_service"))
    out = re.sub(r"%\{ if self_service \}(.*?)%\{ endif \}", (lambda m: m.group(1)) if ss else "", template, flags=re.S)
    out = out.replace('${self_service ? "qic-session" : "classify"}', "qic-session" if ss else "classify")
    return re.sub(r"\$\{(\w+)\}", lambda m: str(values[m.group(1)]), out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--env", default="dev")
    args = ap.parse_args()
    region = tenant_value(args.tenant, "region")
    prefix = f"s7-{args.tenant}-{args.env}"
    s = boto3.Session(region_name=region)
    c = s.client("connect")

    alias = os.environ.get("INSTANCE_ALIAS")
    inst = next(i for i in c.list_instances()["InstanceSummaryList"]
                if i["InstanceAlias"] == alias or i["InstanceAlias"].startswith(prefix))
    iid = inst["Id"]
    queues = {x["Name"]: x["Arn"] for x in c.list_queues(InstanceId=iid, QueueTypes=["STANDARD"])["QueueSummaryList"]}
    hours = {x["Name"]: x["Arn"] for x in c.list_hours_of_operations(InstanceId=iid)["HoursOfOperationSummaryList"]}
    lam = s.client("lambda").get_function(FunctionName=f"{prefix}-genai-gateway")["Configuration"]["FunctionArn"]

    assistant_arn = alias_arn = ""
    try:
        assistants = [a for a in s.client("qconnect").list_assistants()["assistantSummaries"] if a["name"] == f"{prefix}-assistant"]
        assistant_arn = assistants[0]["assistantArn"] if assistants else ""
        lex = s.client("lexv2-models")
        bots = [b for b in lex.list_bots()["botSummaries"] if b["botName"] == f"{prefix}-selfservice"]
        if bots:
            aliases = [a for a in lex.list_bot_aliases(botId=bots[0]["botId"])["botAliasSummaries"] if a["botAliasName"] == "live"]
            if aliases:
                acct = inst["Arn"].split(":")[4]
                alias_arn = f"arn:aws:lex:{region}:{acct}:bot-alias/{bots[0]['botId']}/{aliases[0]['botAliasId']}"
    except ClientError as err:
        print("  (self-service lookup skipped:", err.response["Error"]["Code"], ")")

    if f"{prefix}-general" not in queues or f"{prefix}-business" not in hours:
        print("  skipped: queues/hours not created yet (first deploy) - Terraform will create them in this layer")
        return

    values = dict(gateway_lambda_arn=lam, hours_arn=hours[f"{prefix}-business"],
                  general_queue_arn=queues[f"{prefix}-general"], billing_queue_arn=queues[f"{prefix}-billing"],
                  recorded_participants='["Agent","Customer"]', analytics_enabled="True", analytics_language="en-US",
                  disclosure_text="d", closed_text="c", failure_text="f", goodbye_text="g", self_service_prompt="p",
                  self_service=bool(assistant_arn and alias_arn), qic_assistant_arn=assistant_arn, lex_bot_alias_arn=alias_arn)
    content = render((ROOT / "flows" / "inbound-main.json.tftpl").read_text(), values)
    print(f"Validating inbound flow (self-service {'ON' if values['self_service'] else 'OFF'}) against {inst['InstanceAlias']}")
    try:
        r = c.create_contact_flow(InstanceId=iid, Name=f"zz-flowcheck-{args.tenant}", Type="CONTACT_FLOW", Content=content)
        c.delete_contact_flow(InstanceId=iid, ContactFlowId=r["ContactFlowId"])
        print("  FLOW ACCEPTED")
    except ClientError as err:
        print(json.dumps({k: v for k, v in err.response.items() if k != "ResponseMetadata"}, indent=2, default=str))
        sys.exit(1)


if __name__ == "__main__":
    main()
