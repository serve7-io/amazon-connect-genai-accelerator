#!/usr/bin/env python3
"""Turn a flow exported from any Connect instance into CloudFormation Content with references instead of ARNs.

    python3 templatize-flow.py exported-flow.json [--map arn-map.yaml] [--logical-id SalesInboundFlow --name "Sales Inbound"]

Exported flows (console "Export flow", or `aws connect describe-contact-flow ... --query ContactFlow.Content`)
hard-code the ARNs of the source instance: queues, hours, prompts, other flows, Lambdas, Lex bots. This tool:

  1. finds every ARN in the flow (in Actions and in the console Metadata),
  2. names each one from the flow's own metadata (e.g. queue "Sales" -> ${QueueSales.QueueArn}),
     or from --map when you want to point it at an existing logical id or parameter,
  3. prints a ready-to-paste `AWS::Connect::ContactFlow` resource whose Content is `!Sub` with those references,
  4. lists which resources the template must contain, and FAILS if any account id / instance id is left over.

Map file (optional), one entry per source ARN; the value is what goes inside ${...}:
    arn:aws:connect:ca-central-1:111122223333:instance/abc/queue/def: SalesQueue.QueueArn
    arn:aws:lambda:ca-central-1:111122223333:function:lookup: LookupFunctionArn     # a template parameter
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ARN_RE = re.compile(r"arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:\d{12}:[A-Za-z0-9_./:$@+=,-]+")

# Connect resource path segment -> (CloudFormation type, GetAtt attribute, logical-id prefix)
CONNECT_KINDS = {
    "queue": ("AWS::Connect::Queue", "QueueArn", "Queue"),
    "operating-hours": ("AWS::Connect::HoursOfOperation", "HoursOfOperationArn", "Hours"),
    "prompt": ("AWS::Connect::Prompt", "PromptArn", "Prompt"),
    "contact-flow": ("AWS::Connect::ContactFlow", "ContactFlowArn", "Flow"),
    "flow-module": ("AWS::Connect::ContactFlowModule", "ContactFlowModuleArn", "FlowModule"),
    "routing-profile": ("AWS::Connect::RoutingProfile", "RoutingProfileArn", "RoutingProfile"),
    "agent": ("AWS::Connect::User", "UserArn", "User"),
    "security-profile": ("AWS::Connect::SecurityProfile", "SecurityProfileArn", "SecurityProfile"),
    "transfer-destination": ("AWS::Connect::QuickConnect", "QuickConnectArn", "QuickConnect"),
    "task-template": ("AWS::Connect::TaskTemplate", "Arn", "TaskTemplate"),
    "agent-group": ("AWS::Connect::UserHierarchyGroup", "UserHierarchyGroupArn", "HierarchyGroup"),
}


def classify(arn: str) -> tuple[str, str, str]:
    """-> (cfn type, attribute, logical-id prefix) for an ARN."""
    parts = arn.split(":", 5)
    service, resource = parts[2], parts[5]
    if service == "connect":
        seg = resource.split("/")
        if len(seg) == 2 and seg[0] == "instance":
            return "AWS::Connect::Instance", "Arn", "Instance"
        if len(seg) >= 3 and seg[0] == "instance":
            return CONNECT_KINDS.get(seg[2], ("AWS::Connect::?", "Arn", seg[2].title().replace("-", "")))
        if seg[0] == "phone-number":
            return "AWS::Connect::PhoneNumber", "PhoneNumberArn", "PhoneNumber"
    if service == "lambda":
        return "AWS::Lambda::Function", "Arn", "Function"
    if service == "lex":
        return "AWS::Lex::BotAlias", "Arn", "LexBotAlias"
    return f"AWS::{service}::?", "Arn", service.title()


def metadata_names(node, out: dict[str, str]) -> None:
    """Collect ARN -> display name from console metadata ({"id": arn, "text": name} or {"arn": .., "name": ..})."""
    if isinstance(node, dict):
        ident = node.get("id") or node.get("arn") or node.get("value")
        label = node.get("text") or node.get("name") or node.get("displayName")
        if isinstance(ident, str) and isinstance(label, str) and ARN_RE.fullmatch(ident):
            out.setdefault(ident, label)
        for v in node.values():
            metadata_names(v, out)
    elif isinstance(node, list):
        for v in node:
            metadata_names(v, out)


def pascal(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)
    name = "".join(w[:1].upper() + w[1:] for w in words) or "X"
    return name if name[0].isalpha() else "R" + name


def templatize(flow_json: str, mapping: dict[str, str] | None = None) -> tuple[str, list[dict]]:
    """Return (content with ${...} references, report rows)."""
    mapping = mapping or {}
    flow = json.loads(flow_json)
    names: dict[str, str] = {}
    metadata_names(flow, names)
    text = json.dumps(flow, indent=2)
    arns = sorted(set(ARN_RE.findall(text)), key=len, reverse=True)   # longest first: instance ARN is a prefix
    used_ids: set[str] = set()
    report = []
    for arn in arns:
        cfn_type, attr, prefix = classify(arn)
        if arn in mapping:
            ref = mapping[arn]
        else:
            tail = re.split(r"[/:]", arn.split(":function:")[-1] if ":function:" in arn else arn)[-1]
            fallback = tail[:8] if re.fullmatch(r"[0-9a-f-]{36}", tail) else tail
            logical = prefix + pascal(names.get(arn, fallback))
            base, n = logical, 2
            while logical in used_ids:
                logical, n = f"{base}{n}", n + 1
            used_ids.add(logical)
            ref = f"{logical}.{attr}"
        report.append({"arn": arn, "type": cfn_type, "ref": ref, "name": names.get(arn, "")})
    text = text.replace("${", "${!")                       # literal ${ in the flow must survive Fn::Sub
    for row in report:
        text = text.replace(row["arn"], "${" + row["ref"] + "}")
    return text, report


def leftovers(content: str) -> list[str]:
    """Account ids, instance ids or ARNs still hard-coded after templating."""
    plain = re.sub(r"\$\{[^}]*\}", "", content)
    found = ARN_RE.findall(plain)
    found += re.findall(r"(?<![0-9])\d{12}(?![0-9])", plain)
    found += re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", plain)
    return sorted(set(found))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("flow", help="exported flow JSON file")
    ap.add_argument("--map", help="YAML or JSON file: source ARN -> reference (Logical.Attr or Parameter)")
    ap.add_argument("--logical-id", default="ImportedFlow")
    ap.add_argument("--name", help="flow name in the new instance (default: file name)")
    ap.add_argument("--type", default="CONTACT_FLOW")
    a = ap.parse_args()

    raw = Path(a.flow).read_text()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as err:
        sys.exit(f"{a.flow}: not valid JSON ({err})")
    if isinstance(doc, dict) and "ContactFlow" in doc:            # describe-contact-flow output
        raw = doc["ContactFlow"]["Content"]
    mapping = {}
    if a.map:
        mtext = Path(a.map).read_text()
        try:
            mapping = json.loads(mtext)
        except json.JSONDecodeError:
            import yaml
            mapping = yaml.safe_load(mtext) or {}

    content, report = templatize(raw, mapping)
    left = leftovers(content)

    print(f"# --- {len(report)} ARN(s) replaced (source -> reference -> resource type the template needs)", file=sys.stderr)
    for r in report:
        label = f' "{r["name"]}"' if r["name"] else ""
        print(f"#   {r['arn']}\n#       -> ${{{r['ref']}}}  [{r['type']}]{label}", file=sys.stderr)
    if left:
        print("# !!! still hard-coded (add them to --map or fix the flow):", *left, sep="\n#   ", file=sys.stderr)

    print(f"  {a.logical_id}:")
    print("    Type: AWS::Connect::ContactFlow")
    print("    Properties:")
    print("      InstanceArn: !GetAtt Instance.Arn")
    print(f"      Name: {a.name or Path(a.flow).stem}")
    print(f"      Type: {a.type}")
    print("      State: ACTIVE")
    print("      Content: !Sub |")
    for line in content.splitlines():
        print("        " + line)
    sys.exit(1 if left else 0)


if __name__ == "__main__":
    main()
