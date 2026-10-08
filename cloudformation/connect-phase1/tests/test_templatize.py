"""Round trip: a flow exported from instance A -> template references -> wired to instance B's ARNs."""
import importlib.util
import json
import re
from pathlib import Path

spec = importlib.util.spec_from_file_location("tf", Path(__file__).parents[1] / "templatize-flow.py")
tf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tf)

A = "arn:aws:connect:ca-central-1:111122223333:instance/11111111-2222-3333-4444-555555555555"
EXPORTED = {
    "Version": "2019-10-30",
    "StartAction": "set",
    "Metadata": {"ActionMetadata": {
        "set": {"queue": {"id": f"{A}/queue/aaaaaaaa-0000-0000-0000-000000000001", "text": "Sales"}},
        "hours": {"hours": {"id": f"{A}/operating-hours/bbbbbbbb-0000-0000-0000-000000000002", "text": "Sales Hours"}},
        "play": {"promptName": "Welcome", "prompt": {"id": f"{A}/prompt/cccccccc-0000-0000-0000-000000000003", "text": "Welcome"}},
    }},
    "Actions": [
        {"Identifier": "set", "Type": "UpdateContactTargetQueue",
         "Parameters": {"QueueId": f"{A}/queue/aaaaaaaa-0000-0000-0000-000000000001"},
         "Transitions": {"NextAction": "hours"}},
        {"Identifier": "hours", "Type": "CheckHoursOfOperation",
         "Parameters": {"HoursOfOperationId": f"{A}/operating-hours/bbbbbbbb-0000-0000-0000-000000000002"},
         "Transitions": {"NextAction": "play"}},
        {"Identifier": "play", "Type": "MessageParticipant",
         "Parameters": {"PromptId": f"{A}/prompt/cccccccc-0000-0000-0000-000000000003"},
         "Transitions": {"NextAction": "lambda"}},
        {"Identifier": "lambda", "Type": "InvokeLambdaFunction",
         "Parameters": {"LambdaFunctionARN": "arn:aws:lambda:ca-central-1:111122223333:function:crm-lookup",
                        "InvocationTimeLimitSeconds": "3"},
         "Transitions": {"NextAction": "end"}},
        {"Identifier": "end", "Type": "MessageParticipant",
         "Parameters": {"Text": "Your balance is ${amount} as of $.Attributes.date"}, "Transitions": {}},
    ],
}


def test_every_arn_becomes_a_reference():
    content, report = tf.templatize(json.dumps(EXPORTED))
    refs = {r["ref"] for r in report}
    assert {"QueueSales.QueueArn", "HoursSalesHours.HoursOfOperationArn", "PromptWelcome.PromptArn"} <= refs
    assert any(r.startswith("Function") and r.endswith(".Arn") for r in refs)
    assert tf.leftovers(content) == []
    assert "111122223333" not in content


def test_map_overrides_and_literal_dollar_brace_survives():
    lam = "arn:aws:lambda:ca-central-1:111122223333:function:crm-lookup"
    content, _ = tf.templatize(json.dumps(EXPORTED), {lam: "CrmLookupFunctionArn"})
    assert "${CrmLookupFunctionArn}" in content
    assert "${!amount}" in content            # Fn::Sub turns ${!amount} back into ${amount}


def test_substituted_flow_points_at_the_new_instance_only():
    content, _ = tf.templatize(json.dumps(EXPORTED))
    B = "arn:aws:connect:ca-central-1:999988887777:instance/99999999-8888-7777-6666-555555555555"
    new = {"QueueSales.QueueArn": f"{B}/queue/q1", "HoursSalesHours.HoursOfOperationArn": f"{B}/operating-hours/h1",
           "PromptWelcome.PromptArn": f"{B}/prompt/p1"}

    def fill(m):                              # what CloudFormation's Fn::Sub does
        key = m.group(1)
        if key.startswith("!"):
            return "${" + key[1:] + "}"
        return new.get(key, "arn:aws:lambda:ca-central-1:999988887777:function:crm-lookup")

    rendered = re.sub(r"\$\{([^}]*)\}", fill, content)
    flow = json.loads(rendered)
    assert "111122223333" not in rendered
    assert flow["Actions"][0]["Parameters"]["QueueId"] == f"{B}/queue/q1"
    assert flow["Metadata"]["ActionMetadata"]["set"]["queue"]["id"] == f"{B}/queue/q1"
    assert flow["Actions"][4]["Parameters"]["Text"].startswith("Your balance is ${amount}")
