"""CAA Scenario 1 admin commands (python3 demos/_kit/data.py ccg-s1 <command> ...).

  connect-info      read-only summary of the Connect instance (queues, routing profiles, users, storage,
                    chat/attachment settings, flows, approved origins). Nothing is changed.
  flow-snapshot     READ-ONLY: ccg-s5 stack parameters/status and the live content of ccg-s5-main and
                    ccg-s5-secure-payment (last modified time, block count, keypad settings, full JSON). Changes nothing.
                    Optional flow names: demo: flow-snapshot ccg-s1 <flow name> (names without spaces).
  selftest          live check of ccg-s1-api through the Connect invoke path (no API key needed), then resets Sarah.
  set-proficiency   after Milos creates james.rivera in the console: Roadside Agents routing profile and the
                    roadside_dispatch proficiency (certified, level 5). Changes only that user.
  check-chat-flow   asks Connect why ccg-s1-chat-main is rejected: throwaway flows zz-check-ccg-s1-<n> (deleted again),
                    one block swapped out at a time. The deployment key is never printed.
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


def s5_snapshot(table, scan, *names):
    cf = boto3.client("cloudformation", region_name=REGION)
    st = cf.describe_stacks(StackName="ccg-s5")["Stacks"][0]
    print(f"stack ccg-s5: {st['StackStatus']} last updated {st.get('LastUpdatedTime', st['CreationTime'])}")
    for p in st.get("Parameters", []):
        if p["ParameterKey"] not in ("EncryptionCertificate",):
            print(f"  param {p['ParameterKey']} = {p.get('ParameterValue', '')}")
    c = boto3.client("connect", region_name=REGION)
    flows = {f["Name"]: f for f in _pages(c.list_contact_flows, "ContactFlowSummaryList", InstanceId=INSTANCE)}
    full = {}
    for name in (names or ("ccg-s5-main", "ccg-s5-secure-payment")):
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


def set_proficiency(table, scan, username="james.rivera"):
    """Give the demo agent the roadside_dispatch proficiency (certified, level 5) and the Roadside Agents routing
    profile. The user itself is created by Milos in the console (he sets the password). Changes only this user."""
    c = boto3.client("connect", region_name=REGION)
    users = {u["Username"]: u for u in _pages(c.list_users, "UserSummaryList", InstanceId=INSTANCE)}
    if username not in users:
        raise SystemExit(f"user {username} not found: create it in the Connect console first")
    uid = users[username]["Id"]
    rps = {r["Name"]: r for r in _pages(c.list_routing_profiles, "RoutingProfileSummaryList", InstanceId=INSTANCE)}
    c.update_user_routing_profile(InstanceId=INSTANCE, UserId=uid, RoutingProfileId=rps["Roadside Agents"]["Id"])
    prof = [{"AttributeName": "roadside_dispatch", "AttributeValue": "certified", "Level": 5.0}]
    have = c.list_user_proficiencies(InstanceId=INSTANCE, UserId=uid).get("UserProficiencyList", [])
    if any(p["AttributeName"] == "roadside_dispatch" for p in have):
        c.update_user_proficiencies(InstanceId=INSTANCE, UserId=uid, UserProficiencies=prof)
    else:
        c.associate_user_proficiencies(InstanceId=INSTANCE, UserId=uid, UserProficiencies=prof)
    print(f"{username}: routing profile Roadside Agents, proficiency roadside_dispatch=certified level 5")


def selftest(table, scan):
    """Live check without the API key: invoke ccg-s1-api with Connect-shaped events against the real tables
    (lookup by id and by phone, verify, coverage, dispatch, writeback), then reset Sarah. Also shows that the
    ccg-s5 stack was not updated by this deploy. Touches only ccg-s1-* rows for M-20481537."""
    lam = boto3.client("lambda", region_name=REGION)

    def call(op, **params):
        ev = {"Details": {"Parameters": {"op": op, **params},
                          "ContactData": {"ContactId": "selftest-0000", "Attributes": {}}}}
        out = json.loads(lam.invoke(FunctionName="ccg-s1-api", Payload=json.dumps(ev).encode())["Payload"].read())
        print(f"  {op:<14} {json.dumps(params)[:70]:<72} -> {json.dumps(out)[:400]}")
        return out

    fails = 0
    checks = [
        (call("lookupmember", member_id="M-20481537"), lambda o: o.get("found") == "true" and o.get("prior_calls") == "1"),
        (call("lookupmember", phone="613 555 0152"), lambda o: o.get("member_id") == "M-20481537"),
        (call("lookupmember", member_id="M-99999999"), lambda o: o.get("error") == "member_not_found"),
        (call("verifyservice", member_id="M-20481537", vehicle_confirmed="Honda CR-V"), lambda o: o.get("verified") == "true"),
        (call("verifyservice", member_id="M-20481537", vehicle_confirmed="Ford"), lambda o: o.get("verified") == "false"),
        (call("coverage", member_id="M-20481537", service="tow"), lambda o: o.get("tow_km_included") == "200"),
    ]
    d = call("dispatch", member_id="M-20481537", location="Hwy 401 EB near Joyceville Rd, Kingston", service="tow")
    checks.append((d, lambda o: o.get("driver") == "Mike T." and o.get("eta_min_low") == "25"))
    w = call("writeback", member_id="M-20481537", disposition="Roadside, Tow Dispatched",
             dispatch_id=d.get("dispatch_id", ""), summary="selftest")
    checks.append((w, lambda o: o.get("written") == "true"))
    checks.append((call("reset", member_id="M-20481537"), lambda o: o.get("ok") == "true" and o.get("removed") == "2"))
    for out, ok in checks:
        fails += not ok(out)
    cf = boto3.client("cloudformation", region_name=REGION)
    st = cf.describe_stacks(StackName="ccg-s5")["Stacks"][0]
    print(f"\nccg-s5 stack: {st['StackStatus']} last updated {st.get('LastUpdatedTime')} (expected 2026-10-14 17:11 UTC)")
    print(f"\n{len(checks) - fails}/{len(checks)} checks passed")
    if fails:
        raise SystemExit(1)


def render_chat_flow(values):
    """The ChatFlow content from template.yaml with ${...} replaced from `values` (offline-testable)."""
    import re
    from pathlib import Path
    text = (Path(__file__).resolve().parent / "template.yaml").read_text(encoding="utf-8")
    body = text.split("ChatFlow:", 1)[1].split("Content: !Sub\n        - |\n", 1)[1].split("\n        - {}", 1)[0]
    body = "\n".join(line[10:] for line in body.splitlines())
    return json.loads(re.sub(r"\$\{([\w.]+)\}", lambda m: str(values[m.group(1)]), body))


def _passthrough(action):
    """Swap one block for a plain message that keeps the same next step (used to find the block Connect rejects)."""
    nxt = action["Transitions"].get("NextAction") or "bye"
    return {"Identifier": action["Identifier"], "Type": "MessageParticipant", "Parameters": {"Text": "check"},
            "Transitions": {"NextAction": nxt, "Errors": [{"NextAction": nxt, "ErrorType": "NoMatchingError"}]}}


def check_chat_flow(table, scan):
    """Ask Connect why ccg-s1-chat-main is rejected. Renders the flow with the stack's real ARNs and the app's
    deployment key (never printed), creates throwaway flows zz-check-ccg-s1-<n> (deleted again) and swaps one block at a
    time for a plain message. Changes nothing else."""
    cf = boto3.client("cloudformation", region_name=REGION)
    st = cf.describe_stacks(StackName="demo-ccg-s1")["Stacks"][0]
    out = {o["OutputKey"]: o["OutputValue"] for o in st.get("Outputs", [])}
    par = {p["ParameterKey"]: p.get("ParameterValue", "") for p in st.get("Parameters", [])}
    sm = boto3.client("secretsmanager", region_name=REGION)
    key = json.loads(sm.get_secret_value(SecretId="demo-kit/web/ccg-s1/acxd")["SecretString"]).get("deploymentKey", "")
    print("deployment key length", len(key))
    values = {"GuideFlow.ContactFlowArn": out["GuideFlowArn"], "ApiFunction.Arn": out["ApiFunctionArn"],
              "RoadsideQueue.QueueArn": out["RoadsideQueueArn"], "RoadsideOverflowSeconds": par.get("RoadsideOverflowSeconds") or 90,
              "AcxdWorkspaceId": par.get("AcxdWorkspaceId") or "f2e8e16b-df1e-4177-a3d8-42b833becf81",
              "AcxdApplicationId": "9088c019-349f-4343-b662-6d9c70a168f6", "AcxdAlias": key}
    base = render_chat_flow(values)
    connect = boto3.client("connect", region_name=REGION)

    def variant(swap=(), mutate=None):
        doc = json.loads(json.dumps(base))
        doc["Actions"] = [_passthrough(a) if a["Identifier"] in swap else a for a in doc["Actions"]]
        if mutate:
            mutate({a["Identifier"]: a for a in doc["Actions"]})
        return json.dumps(doc)

    variants = [("control: as CloudFormation sends it", variant())]
    blocks = [a["Identifier"] for a in base["Actions"] if a["Type"] not in ("MessageParticipant", "DisconnectParticipant")]
    for ident in blocks:
        variants.append((f"without '{ident}'", variant({ident})))
    for n, (label, content) in enumerate(variants):
        try:
            r = connect.create_contact_flow(InstanceId=INSTANCE, Name=f"zz-check-ccg-s1-{n}", Type="CONTACT_FLOW", Content=content)
        except Exception as e:  # noqa: BLE001 - we want Connect's whole answer
            resp = getattr(e, "response", {}) or {}
            detail = resp.get("problems") or resp.get("Problems") or resp.get("Error", {}).get("Message") or repr(e)
            print(f"REJECTED  {label}\n          {json.dumps(detail)[:1500]}")
            continue
        connect.delete_contact_flow(InstanceId=INSTANCE, ContactFlowId=r["ContactFlowId"])
        print(f"ACCEPTED  {label}")


COMMANDS = {"selftest": selftest, "connect-info": connect_info, "flow-snapshot": s5_snapshot, "set-proficiency": set_proficiency,
            "check-chat-flow": check_chat_flow}
