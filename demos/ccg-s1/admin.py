"""CAA Scenario 1 admin commands (python3 demos/_kit/data.py ccg-s1 <command> ...).

  connect-info      read-only summary of the Connect instance (queues, routing profiles, users, storage,
                    chat/attachment settings, flows, approved origins). Nothing is changed.
  flow-snapshot     READ-ONLY: ccg-s5 stack parameters/status and the live content of ccg-s5-main and
                    ccg-s5-secure-payment (last modified time, block count, keypad settings, full JSON). Changes nothing.
  add-persona       upsert ONLY the Scenario 1 persona (Sarah Mitchell, M-20481537) from demos/ccg-s5/data/synthetic.json
                    into the shared CRM table ccg-s5-members. No other row, flow or stack is touched.
"""
import json

import boto3

INSTANCE = "78261fe2-310c-4f82-8437-4239487463ac"
REGION = "ca-central-1"


def _pages(fn, key, **kw):
    out = []
    while True:
        r = fn(**kw)
        out += r.get(key, [])
        if not r.get("NextToken"):
            return out
        kw["NextToken"] = r["NextToken"]


def connect_info(table, scan):
    c = boto3.client("connect", region_name=REGION)
    inst = c.describe_instance(InstanceId=INSTANCE)["Instance"]
    print(f"instance {inst['InstanceAlias']} identity={inst['IdentityManagementType']} "
          f"inbound={inst.get('InboundCallsEnabled')} outbound={inst.get('OutboundCallsEnabled')}")
    for a in ("CONTACT_LENS", "MULTI_PARTY_CHAT_CONFERENCE", "ENHANCED_CHAT_MONITORING", "AUTO_RESOLVE_BEST_VOICES",
              "EARLY_MEDIA", "CONTACTFLOW_LOGS", "USE_CUSTOM_TTS_VOICES"):
        try:
            v = c.describe_instance_attribute(InstanceId=INSTANCE, AttributeType=a)["Attribute"]["Value"]
            print(f"  attribute {a} = {v}")
        except Exception as e:  # noqa: BLE001
            print(f"  attribute {a}: {type(e).__name__}")
    for rt in ("CHAT_TRANSCRIPTS", "CALL_RECORDINGS", "ATTACHMENTS", "SCHEDULED_REPORTS", "CONTACT_EVALUATIONS"):
        try:
            sc = c.list_instance_storage_configs(InstanceId=INSTANCE, ResourceType=rt)["StorageConfigs"]
            print(f"  storage {rt}: " + (", ".join(
                f"{s['StorageType']}:{(s.get('S3Config') or {}).get('BucketName', '')}/{(s.get('S3Config') or {}).get('BucketPrefix', '')}"
                for s in sc) or "none"))
        except Exception as e:  # noqa: BLE001
            print(f"  storage {rt}: {type(e).__name__}")
    print("\nqueues:", ", ".join(q["Name"] for q in _pages(c.list_queues, "QueueSummaryList", InstanceId=INSTANCE,
                                                              QueueTypes=["STANDARD"])))
    print("routing profiles:", ", ".join(r["Name"] for r in _pages(c.list_routing_profiles, "RoutingProfileSummaryList",
                                                                    InstanceId=INSTANCE)))
    print("security profiles:", ", ".join(r["Name"] for r in _pages(c.list_security_profiles, "SecurityProfileSummaryList",
                                                                     InstanceId=INSTANCE)))
    users = _pages(c.list_users, "UserSummaryList", InstanceId=INSTANCE)
    print("users:", ", ".join(u["Username"] for u in users))
    print("hours:", ", ".join(h["Name"] for h in _pages(c.list_hours_of_operations, "HoursOfOperationSummaryList",
                                                        InstanceId=INSTANCE)))
    flows = _pages(c.list_contact_flows, "ContactFlowSummaryList", InstanceId=INSTANCE)
    print("flows (custom):", ", ".join(f"{f['Name']} [{f['ContactFlowType']}]" for f in flows
                                       if not f["Name"].startswith("Default") and not f["Name"].startswith("Sample")))
    print("phone numbers:", ", ".join(p["PhoneNumber"] for p in _pages(c.list_phone_numbers_v2, "ListPhoneNumbersSummaryList",
                                                                       TargetArn=inst["Arn"])))
    try:
        print("approved origins:", c.list_approved_origins(InstanceId=INSTANCE).get("Origins"))
    except Exception as e:  # noqa: BLE001
        print("approved origins:", type(e).__name__)
    try:
        views = _pages(c.list_views, "ViewsSummaryList", InstanceId=INSTANCE, Type="CUSTOMER_MANAGED")
        print("custom views (step-by-step guides):", ", ".join(v["Name"] for v in views) or "none")
    except Exception as e:  # noqa: BLE001
        print("views:", type(e).__name__)
    try:
        assoc = c.list_integration_associations(InstanceId=INSTANCE)["IntegrationAssociationSummaryList"]
        print("integrations:", json.dumps(sorted({a["IntegrationType"] for a in assoc})))
    except Exception as e:  # noqa: BLE001
        print("integrations:", type(e).__name__)


def s5_snapshot(table, scan):
    cf = boto3.client("cloudformation", region_name=REGION)
    st = cf.describe_stacks(StackName="ccg-s5")["Stacks"][0]
    print(f"stack ccg-s5: {st['StackStatus']} last updated {st.get('LastUpdatedTime', st['CreationTime'])}")
    for p in st.get("Parameters", []):
        if p["ParameterKey"] not in ("EncryptionCertificate",):
            print(f"  param {p['ParameterKey']} = {p.get('ParameterValue', '')}")
    c = boto3.client("connect", region_name=REGION)
    flows = {f["Name"]: f for f in _pages(c.list_contact_flows, "ContactFlowSummaryList", InstanceId=INSTANCE)}
    full = {}
    for name in ("ccg-s5-main", "ccg-s5-secure-payment"):
        f = flows.get(name)
        if not f:
            print(f"\n{name}: not found")
            continue
        d = c.describe_contact_flow(InstanceId=INSTANCE, ContactFlowId=f["Id"])["ContactFlow"]
        content = json.loads(d["Content"])
        acts = content.get("Actions", [])
        print(f"\n{name} ({f['Id']}): state={d.get('State')} status={d.get('Status')} "
              f"last_modified={d.get('LastModifiedTime')} blocks={len(acts)}")
        for a in acts:
            p = a.get("Parameters", {})
            line = f"  - {a.get('Identifier', '')[:40]:<40} {a['Type']}"
            if a["Type"] == "GetParticipantInput":
                keep = {k: v for k, v in p.items() if k not in ("InputEncryption",)}
                line += " " + json.dumps(keep)
            elif a["Type"] == "ConnectParticipantWithAgenticCX":
                line += " " + json.dumps(p)
            elif a["Type"] == "Compare":
                line += " " + json.dumps(p) + " -> " + json.dumps(a.get("Transitions", {}).get("Conditions", []))
            elif a["Type"] == "MessageParticipant":
                line += " " + json.dumps(p.get("Text") or p.get("SSML") or "")[:160]
            print(line)
        try:
            vers = c.list_contact_flow_versions(InstanceId=INSTANCE, ContactFlowId=f["Id"]).get("ContactFlowVersionSummaryList", [])
            for v in vers:
                print(f"  version {v.get('Version')}: {v.get('VersionDescription', '')}")
        except Exception as e:  # noqa: BLE001
            print(f"  versions: {type(e).__name__}")
        if name == "ccg-s5-secure-payment":
            for a in acts:
                if a["Type"] == "GetParticipantInput":
                    a["Parameters"].get("InputEncryption", {}).pop("Key", None)  # long public cert, not needed here
        full[name] = content
    print("\n===== FULL FLOW JSON (for committing to the repo) =====")
    print(json.dumps(full, separators=(",", ":")))


PERSONAS = ("M-20481537",)


def add_persona(table, scan):
    from decimal import Decimal
    from pathlib import Path
    data = json.loads((Path(__file__).resolve().parents[1] / "ccg-s5" / "data" / "synthetic.json").read_text(),
                      parse_float=Decimal)
    t = boto3.resource("dynamodb", region_name=REGION).Table("ccg-s5-members")
    for m in data["members"]:
        if m["member_id"] in PERSONAS:
            t.put_item(Item=m)
            print(f"upserted {m['member_id']} {m['name']} ({m.get('tier')}, {m.get('vehicle')})")


COMMANDS = {"connect-info": connect_info, "add-persona": add_persona, "flow-snapshot": s5_snapshot}
