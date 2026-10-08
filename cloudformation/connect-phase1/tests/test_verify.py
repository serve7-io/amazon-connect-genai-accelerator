"""verify.py against a fake Connect API: correct wiring passes, a foreign queue ARN in a flow fails."""
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
I = "arn:aws:connect:ca-central-1:123456789012:instance/inst-1"
OUT = {
    "InstanceId": "inst-1", "InstanceArn": I, "AccessUrl": "https://x.my.connect.aws",
    "SalesHoursArn": f"{I}/operating-hours/h-sales", "SupportHoursArn": f"{I}/operating-hours/h-support",
    "SalesQueueArn": f"{I}/queue/q-sales", "SupportQueueArn": f"{I}/queue/q-support",
    "RoutingProfileArn": f"{I}/routing-profile/rp-1", "AgentSecurityProfileArn": f"{I}/security-profile/sp-1",
    "Agent1Arn": f"{I}/agent/u1", "Agent2Arn": f"{I}/agent/u2",
    "SalesInboundFlowArn": f"{I}/contact-flow/f-sales", "SupportInboundFlowArn": f"{I}/contact-flow/f-support",
    "StorageBucket": "bucket-1", "Agent1PasswordSecret": "s1", "Agent2PasswordSecret": "s2",
}


def template_flow(logical: str, subs: dict) -> str:
    """The real flow content from template.yaml, rendered the way Fn::Sub would."""
    class L(yaml.SafeLoader):
        pass
    for tag in ("!Sub", "!Ref", "!GetAtt", "!If", "!Not", "!Equals"):
        L.add_constructor(tag, lambda l, n, t=tag: {t: l.construct_scalar(n) if isinstance(n, yaml.ScalarNode) else l.construct_sequence(n)})
    t = yaml.load((ROOT / "template.yaml").read_text(), Loader=L)
    content = t["Resources"][logical]["Properties"]["Content"]["!Sub"]
    return re.sub(r"\$\{([^}]+)\}", lambda m: subs[m.group(1)], content)


class FakeConnect:
    def __init__(self, sales_queue_in_flow):
        self.flows = {
            "f-sales": template_flow("SalesInboundFlow", {"SalesQueue.QueueArn": sales_queue_in_flow,
                                                          "SalesHours.HoursOfOperationArn": OUT["SalesHoursArn"]}),
            "f-support": template_flow("SupportInboundFlow", {"SupportQueue.QueueArn": OUT["SupportQueueArn"],
                                                              "SupportHours.HoursOfOperationArn": OUT["SupportHoursArn"]}),
        }
    def describe_instance_attribute(self, InstanceId, AttributeType):
        return {"Attribute": {"Value": "true"}}
    def list_instance_storage_configs(self, InstanceId, ResourceType):
        return {"StorageConfigs": [{"S3Config": {"BucketName": "bucket-1"}}]}
    def describe_queue(self, InstanceId, QueueId):
        return {"Queue": {"Name": QueueId, "HoursOfOperationId": {"q-sales": "h-sales", "q-support": "h-support"}[QueueId]}}
    def describe_routing_profile(self, InstanceId, RoutingProfileId):
        return {"RoutingProfile": {"Name": "Sales and Support Agents", "DefaultOutboundQueueId": "q-support"}}
    def list_routing_profile_queues(self, InstanceId, RoutingProfileId):
        return {"RoutingProfileQueueConfigSummaryList": [
            {"QueueArn": OUT[q], "Channel": c} for q in ("SalesQueueArn", "SupportQueueArn") for c in ("VOICE", "CHAT")]}
    def describe_user(self, InstanceId, UserId):
        return {"User": {"Username": UserId, "RoutingProfileId": "rp-1", "SecurityProfileIds": ["sp-1"]}}
    def describe_contact_flow(self, InstanceId, ContactFlowId):
        return {"ContactFlow": {"Name": ContactFlowId, "State": "ACTIVE", "Content": self.flows[ContactFlowId]}}


def run(monkeypatch, connect):
    cfn = types.SimpleNamespace(describe_stacks=lambda StackName: {"Stacks": [{"Outputs": [
        {"OutputKey": k, "OutputValue": v} for k, v in OUT.items()]}]})
    fake_boto3 = types.SimpleNamespace(client=lambda name: {"cloudformation": cfn, "connect": connect}[name])
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    spec = importlib.util.spec_from_file_location("verify", ROOT / "verify.py")
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    monkeypatch.setattr(sys, "argv", ["verify.py"])
    with pytest.raises(SystemExit) as e:
        v.main()
    return e.value.code, v.results


def test_correct_wiring_passes(monkeypatch):
    code, results = run(monkeypatch, FakeConnect(OUT["SalesQueueArn"]))
    assert code == 0, [w for ok, w in results if not ok]
    assert len(results) >= 20


def test_flow_pointing_at_another_instance_fails(monkeypatch):
    foreign = "arn:aws:connect:ca-central-1:999999999999:instance/other/queue/q-old"
    code, results = run(monkeypatch, FakeConnect(foreign))
    failed = [w for ok, w in results if not ok]
    assert code == 1
    assert any("belong to this instance" in w for w in failed)
    assert any("routes to SalesQueue" in w for w in failed)
