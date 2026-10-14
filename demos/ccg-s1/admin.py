"""CAA Scenario 1 admin commands (python3 demos/_kit/data.py ccg-s1 <command> ...).

  connect-info      read-only summary of the Connect instance (queues, routing profiles, users, storage,
                    chat/attachment settings, flows, approved origins). Nothing is changed.
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


COMMANDS = {"connect-info": connect_info}
