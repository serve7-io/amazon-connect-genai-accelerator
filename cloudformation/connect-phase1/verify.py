#!/usr/bin/env python3
"""Check that a deployed Phase 1 stack is wired to itself (run in CloudShell after phase1.sh deploy).

    python3 verify.py [--stack s7-connect-phase1]

Checks, all against the live instance (not the template):
  * instance attributes: Contact Lens and flow logs on
  * recordings + chat transcripts storage -> this stack's bucket
  * each queue -> its own hours of operation
  * routing profile -> exactly Sales + Support on voice and chat; default outbound queue
  * each agent -> the routing profile and the agent security profile
  * each flow -> ACTIVE, Contact Lens + logging blocks present, every ARN inside belongs to THIS instance and to a
    resource of THIS stack, and it routes to the expected queue
  * phone number (if claimed) -> this instance and the Support Inbound flow
Exit code 1 when anything fails.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import boto3

ARN_RE = re.compile(r"arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:\d{12}:[A-Za-z0-9_./:-]+")
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((bool(ok), what))
    print(f"  [{'PASS' if ok else 'FAIL'}] {what}")


def tail(arn: str) -> str:
    return arn.rsplit("/", 1)[-1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", default="s7-connect-phase1")
    a = ap.parse_args()
    cfn, connect = boto3.client("cloudformation"), boto3.client("connect")
    o = {x["OutputKey"]: x["OutputValue"] for x in cfn.describe_stacks(StackName=a.stack)["Stacks"][0]["Outputs"]}
    iid, iarn = o["InstanceId"], o["InstanceArn"]
    print(f"Stack {a.stack} -> instance {iid} ({o['AccessUrl']})")

    print("Instance")
    for attr in ("CONTACT_LENS", "CONTACTFLOW_LOGS"):
        val = connect.describe_instance_attribute(InstanceId=iid, AttributeType=attr)["Attribute"]["Value"]
        check(val == "true", f"{attr} = {val}")
    for rtype in ("CALL_RECORDINGS", "CHAT_TRANSCRIPTS"):
        cfgs = connect.list_instance_storage_configs(InstanceId=iid, ResourceType=rtype)["StorageConfigs"]
        buckets = [c.get("S3Config", {}).get("BucketName") for c in cfgs]
        check(o["StorageBucket"] in buckets, f"{rtype} -> s3://{o['StorageBucket']}")

    print("Queues -> hours of operation")
    for q, h in (("SalesQueueArn", "SalesHoursArn"), ("SupportQueueArn", "SupportHoursArn")):
        queue = connect.describe_queue(InstanceId=iid, QueueId=tail(o[q]))["Queue"]
        check(queue["HoursOfOperationId"] == tail(o[h]), f"queue {queue['Name']} -> {h.replace('Arn', '')}")

    print("Routing profile -> queues")
    rp_id = tail(o["RoutingProfileArn"])
    rp = connect.describe_routing_profile(InstanceId=iid, RoutingProfileId=rp_id)["RoutingProfile"]
    links = connect.list_routing_profile_queues(InstanceId=iid, RoutingProfileId=rp_id)["RoutingProfileQueueConfigSummaryList"]
    got = {(tail(x["QueueArn"]), x["Channel"]) for x in links}
    want = {(tail(o[q]), ch) for q in ("SalesQueueArn", "SupportQueueArn") for ch in ("VOICE", "CHAT")}
    check(got == want, f"'{rp['Name']}' serves Sales + Support on VOICE + CHAT ({len(got)} links)")
    check(rp["DefaultOutboundQueueId"] == tail(o["SupportQueueArn"]), "default outbound queue = Support")

    print("Agents -> routing profile + security profile")
    for key in ("Agent1Arn", "Agent2Arn"):
        user = connect.describe_user(InstanceId=iid, UserId=tail(o[key]))["User"]
        check(user["RoutingProfileId"] == rp_id, f"user {user['Username']} -> routing profile '{rp['Name']}'")
        check(tail(o["AgentSecurityProfileArn"]) in user["SecurityProfileIds"], f"user {user['Username']} -> Phase1 Agent profile")

    print("Flows -> this instance's resources")
    stack_arns = {v for v in o.values() if v.startswith("arn:")}
    for key, queue_key in (("SalesInboundFlowArn", "SalesQueueArn"), ("SupportInboundFlowArn", "SupportQueueArn")):
        flow = connect.describe_contact_flow(InstanceId=iid, ContactFlowId=tail(o[key]))["ContactFlow"]
        content = flow["Content"]
        arns = set(ARN_RE.findall(content))
        name = flow["Name"]
        check(flow.get("State") == "ACTIVE", f"flow '{name}' is ACTIVE")
        check(all(x.startswith(iarn + "/") for x in arns), f"flow '{name}': all {len(arns)} ARN(s) belong to this instance")
        check(arns <= stack_arns, f"flow '{name}': every ARN is a resource of this stack")
        check(o[queue_key] in arns, f"flow '{name}' routes to {queue_key.replace('Arn', '')}")
        actions = {x["Type"]: x for x in json.loads(content)["Actions"]}
        rec = actions.get("UpdateContactRecordingBehavior", {}).get("Parameters", {})
        check(rec.get("AnalyticsBehavior", {}).get("Enabled") == "True", f"flow '{name}': Contact Lens enabled in the flow")
        check("UpdateFlowLoggingBehavior" in actions, f"flow '{name}': flow logging block present")

    if "PhoneNumberArn" in o:
        print("Phone number -> flow")
        num = connect.describe_phone_number(PhoneNumberId=tail(o["PhoneNumberArn"]))["ClaimedPhoneNumberSummary"]
        check(num["TargetArn"] == iarn, f"{num['PhoneNumber']} claimed for this instance")
        assoc = connect.list_flow_associations(InstanceId=iid, ResourceType="VOICE_PHONE_NUMBER")["FlowAssociationSummaryList"]
        flows = {tail(x["FlowId"]) for x in assoc if tail(x["ResourceId"]) == tail(o["PhoneNumberArn"])}
        check(tail(o["SupportInboundFlowArn"]) in flows, f"{num['PhoneNumber']} -> Support Inbound flow")

    failed = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
