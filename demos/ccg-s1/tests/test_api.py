"""Offline tests for ccg-s1-api (no AWS): an in-memory table fake replaces DynamoDB."""
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SARAH = "M-20481537"


class FakeDb:
    def __init__(self, seed):
        self.rows = {name: [dict(r) for r in rows] for name, rows in seed["tables"].items()}

    def _keys(self, table):
        return self.h.KEYS[table]

    def get(self, table, key):
        return next((r for r in self.rows[table] if all(r.get(k) == v for k, v in key.items())), None)

    def put(self, table, item):
        keys = self._keys(table)
        self.rows[table] = [r for r in self.rows[table] if any(r.get(k) != item.get(k) for k in keys)] + [dict(item)]

    def delete(self, table, key):
        self.rows[table] = [r for r in self.rows[table] if any(r.get(k) != v for k, v in key.items())]

    def query(self, table, attr, value, index=None):
        return [r for r in self.rows[table] if r.get(attr) == value]

    def scan(self, table):
        return list(self.rows[table])


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    sys.path.insert(0, str(ROOT / "lambdas" / "api"))
    sys.modules.pop("handler", None)
    import handler
    fake = FakeDb(handler.SEED)
    fake.h = handler
    monkeypatch.setattr(handler, "DB", fake)
    yield types.SimpleNamespace(h=handler, db=fake)
    sys.path.remove(str(ROOT / "lambdas" / "api"))


def call(api, op, body, key="k"):
    r = api.h.handler({"headers": {"x-api-key": key}, "rawPath": "/" + op, "body": json.dumps(body),
                       "requestContext": {"http": {"method": "POST"}}}, None)
    return r["statusCode"], json.loads(r["body"])


def test_every_success_and_error_has_a_message(api):
    for op, body in [("lookupmember", {"member_id": SARAH}), ("lookupmember", {"member_id": "M-1"}),
                     ("verifyservice", {"member_id": SARAH, "vehicle_confirmed": "2023 Honda CR-V"}),
                     ("verifyservice", {"member_id": SARAH, "vehicle_confirmed": "Ford F-150"}),
                     ("coverage", {"member_id": SARAH, "service": "tow"}), ("coverage", {"member_id": SARAH, "service": "x"}),
                     ("dispatch", {"member_id": SARAH, "location": "Hwy 401 EB near Joyceville Rd"}),
                     ("dispatch", {"member_id": SARAH}),
                     ("writeback", {"contact_id": "c-1", "member_id": SARAH}), ("writeback", {}),
                     ("reset", {"member_id": SARAH})]:
        code, out = call(api, op, body)
        assert code == 200 and out.get("message"), (op, body, out)


def test_lookup_by_member_id_and_by_phone(api):
    out = call(api, "lookupmember", {"member_id": "m 2048 1537"})[1]
    assert out["found"] and out["first_name"] == "Sarah" and out["tier"] == "Plus"
    assert out["vehicle_spoken"] == "2023 Honda CR-V" and out["member_since"] == "2019"
    assert out["prior_calls"] == 1 and out["last_service"] == "battery boost, March 2025" and out["open_cases"] == 0
    assert "—" not in out["message"]
    assert call(api, "lookupmember", {"phone": "(613) 555-0152"})[1]["member_id"] == SARAH


def test_lookup_not_found_offers_alternatives(api):
    out = call(api, "lookupmember", {"member_id": "M-99999999"})[1]
    assert out == {"found": False, "error": "member_not_found", "valid_options": ["member_id", "phone"],
                   "message": "I couldn't find that membership number. Could you check it, or give me the phone number on the account?"}


@pytest.mark.parametrize("body,ok", [
    ({"interaction_type": "service", "vehicle_confirmed": "2023 Honda CR-V"}, True),
    ({"interaction_type": "service", "vehicle_confirmed": "yes"}, True),
    ({"interaction_type": "service", "vehicle_confirmed": "a Ford F-150"}, False),
    ({"interaction_type": "account_change", "postal_code": "k7l3n6", "phone": "613-555-0152"}, True),
    ({"interaction_type": "account_change", "postal_code": "K7L 3N6", "phone": "613-555-0199"}, False),
    ({"interaction_type": "payment", "dob": "April 22 1986", "postal_code": "K7L 3N6"}, True),
    ({"interaction_type": "payment", "dob": "1986-04-23", "postal_code": "K7L 3N6"}, False),
])
def test_verifyservice_rule_table(api, body, ok):
    out = call(api, "verifyservice", {"member_id": SARAH, **body})[1]
    assert out["verified"] is ok
    if ok:
        assert out["auth_level"] == body["interaction_type"]
    else:
        assert out["error"] == "not_verified" and "vehicle" not in out["message"].lower()  # never names the factor


def test_verifyservice_missing_and_unknown_type(api):
    assert call(api, "verifyservice", {"member_id": SARAH})[1]["valid_options"] == ["vehicle_confirmed"]
    assert call(api, "verifyservice", {"member_id": SARAH, "interaction_type": "x"})[1]["error"] == "unknown_interaction_type"


def test_coverage_plus_tow_matches_script(api):
    out = call(api, "coverage", {"member_id": SARAH, "service": "tow"})[1]
    assert out["covered"] and out["tow_km_included"] == 200 and out["cost_to_member"] == 0
    assert out["message"] == "Your Plus membership covers up to 200 km of towing at no charge."
    assert out["demo_value"] is False
    assert call(api, "coverage", {"member_id": "M-31570264"})[1]["demo_value"] is True


def test_coverage_echoes_intake_and_builds_hand_off_summary(api):
    out = call(api, "coverage", {"member_id": SARAH, "service": "tow", "location": "Hwy 401 EB near Joyceville Rd",
                                 "safety": "safe, off the road", "spare": "no spare"})[1]
    assert (out["location"], out["safety"], out["spare"]) == ("Hwy 401 EB near Joyceville Rd", "safe, off the road", "no spare")
    assert out["summary"] == ("Sarah Mitchell, Plus member M-20481537. Vehicle: 2023 Honda CR-V. "
                              "Location: Hwy 401 EB near Joyceville Rd. Safety: safe, off the road. Spare tire: no spare. "
                              "Needs towing, 200 km covered at no charge.")
    assert call(api, "verifyservice", {"member_id": SARAH, "vehicle_confirmed": "yes"})[1]["auth_status"] == "Verified"


def test_dispatch_writes_record_and_returns_script_values(api):
    out = call(api, "dispatch", {"member_id": SARAH, "location": "Hwy 401 EB near Joyceville Rd, Kingston",
                                 "service": "tow", "destination": "nearest_certified"})[1]
    assert out["provider"] == "Kingston Auto Services" and out["driver"] == "Mike T."
    assert (out["eta_min_low"], out["eta_min_high"]) == (25, 30)
    assert out["destination"] == "certified repair shop, Princess Street, Kingston"
    assert api.db.get("DispatchesTable", {"dispatch_id": out["dispatch_id"]})["member_id"] == SARAH


def test_writeback_and_reset(api):
    d = call(api, "dispatch", {"member_id": SARAH, "location": "Hwy 401"})[1]
    w = call(api, "writeback", {"contact_id": "c-1", "member_id": SARAH, "disposition": "Roadside, Tow Dispatched",
                                "dispatch_id": d["dispatch_id"], "summary": "Flat tire, no spare."})[1]
    assert w["written"] and w["record_id"].startswith("SN-INC")
    api.db.put("MembersTable", {**api.db.get("MembersTable", {"member_id": SARAH}), "open_cases": 3})
    r = call(api, "reset", {"member_id": SARAH})[1]
    assert r["ok"] and r["removed"] == 2
    assert api.db.get("MembersTable", {"member_id": SARAH})["open_cases"] == 0
    assert api.db.scan("DispatchesTable") == [] and api.db.scan("ServiceRecordsTable") == []


def test_connect_invoke_returns_flat_string_map(api):
    ev = {"Details": {"Parameters": {"op": "lookupmember"},
                      "ContactData": {"ContactId": "c-9", "Attributes": {"s1_member_id": SARAH}}}}
    out = api.h.handler(ev, None)
    assert all(isinstance(v, str) for v in out.values())
    assert out["found"] == "true" and out["prior_calls"] == "1" and out["first_name"] == "Sarah"
    out = api.h.handler({"Details": {"Parameters": {"op": "dispatch", "location": "Hwy 401"},
                                     "ContactData": {"ContactId": "c-9", "Attributes": {"s1_member_id": SARAH}}}}, None)
    assert out["dispatched"] == "true" and out["driver"] == "Mike T."
    assert api.h.handler({"Details": {"Parameters": {"op": "nope"}}}, None)["error"] == "unknown_operation"


def test_key_health_portal_and_context_contact_id(api):
    assert call(api, "lookupmember", {"member_id": SARAH}, key="wrong")[0] == 401
    h = api.h.handler({"rawPath": "/health", "requestContext": {"http": {"method": "POST"}}}, None)
    assert json.loads(h["body"])["units"] == 3
    page = api.h.handler({"rawPath": "/", "requestContext": {"http": {"method": "GET"}}}, None)
    assert page["statusCode"] == 200 and "CAA Club Group" in page["body"]
    cid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    out = call(api, "writeback", {"contact_id": "{contact_id}", "member_id": SARAH, "nlx_context": {"v": {"contact_id": cid}}})[1]
    assert api.db.get("ServiceRecordsTable", {"contact_id": cid})["record_id"] == out["record_id"]


def test_start_chat_uses_server_side_flow_and_sanitizes(api, monkeypatch):
    calls = {}

    class FakeConnect:
        def list_contact_flows(self, **kw):
            return {"ContactFlowSummaryList": [{"Name": "other", "Id": "f-0"}, {"Name": "ccg-s1-chat-main", "Id": "f-1"}]}

        def start_chat_contact(self, **kw):
            calls.update(kw)
            return {"ContactId": "c-1", "ParticipantId": "p-1", "ParticipantToken": "t-1"}

    monkeypatch.setenv("INSTANCE_ID", "inst-1")
    monkeypatch.setattr(api.h, "CONNECT", FakeConnect())
    api.h._FLOW_ID.clear()
    body = {"InstanceId": "evil", "ContactFlowId": "evil", "ParticipantDetails": {"DisplayName": "Sarah<script>"},
            "Attributes": {"x": "y"}, "SupportedMessagingContentTypes": ["text/plain", "text/html"]}
    r = api.h.handler({"rawPath": "/start-chat", "body": json.dumps(body), "requestContext": {"http": {"method": "POST"}}}, None)
    assert r["statusCode"] == 200
    assert json.loads(r["body"]) == {"data": {"startChatResult": {"ContactId": "c-1", "ParticipantId": "p-1", "ParticipantToken": "t-1"}}}
    assert calls["InstanceId"] == "inst-1" and calls["ContactFlowId"] == "f-1"
    assert calls["ParticipantDetails"] == {"DisplayName": "Sarahscript"} and calls["Attributes"] == {"s1_entry": "portal"}
    assert calls["SupportedMessagingContentTypes"] == ["text/plain"]


def test_start_chat_not_ready_and_portal_loads_touchpoint(api, monkeypatch):
    class NoFlow:
        def list_contact_flows(self, **kw):
            return {"ContactFlowSummaryList": []}

    monkeypatch.setenv("INSTANCE_ID", "inst-1")
    monkeypatch.setattr(api.h, "CONNECT", NoFlow())
    api.h._FLOW_ID.clear()
    r = api.h.handler({"rawPath": "/start-chat", "body": "{}", "requestContext": {"http": {"method": "POST"}}}, None)
    assert r["statusCode"] == 503 and json.loads(r["body"])["error"] == "chat_not_ready"
    page = api.h.handler({"rawPath": "/", "requestContext": {"http": {"method": "GET"}}}, None)["body"]
    assert "@amazon-connect-touchpoint/web@1.0.3/lib/index.umd.js" in page and '"inst-1"' in page


def test_intake_record_from_tool_calls_reaches_connect(api):
    cid = "6a2b1c4d-0000-4000-8000-0000000000a1"
    ctx = {"nlx_context": {"contact_id": cid}}
    assert call(api, "intake", {"contact_id": cid})[1]["found"] is False
    call(api, "lookupmember", {"member_id": SARAH, **ctx})
    call(api, "verifyservice", {"member_id": SARAH, "vehicle_confirmed": "yes", **ctx})
    call(api, "coverage", {"member_id": SARAH, "service": "tow", "location": "Hwy 401 EB near Joyceville Rd",
                           "safety": "safe, off the road", "spare": "no spare", **ctx})
    call(api, "lookupmember", {"member_id": "M-99999999", **ctx})  # a miss never overwrites the record
    call(api, "coverage", {"member_id": SARAH, "service": "x", **ctx})  # nor does a failed tool call
    out = api.h.handler({"Details": {"Parameters": {"op": "intake"}, "ContactData": {"ContactId": cid}}}, None)
    assert out["found"] == "true" and out["member_id"] == SARAH and out["tier"] == "Plus"
    assert out["vehicle"] == "2023 Honda CR-V" and out["auth_status"] == "Verified" and out["service"] == "tow"
    assert (out["location"], out["safety"], out["spare"]) == ("Hwy 401 EB near Joyceville Rd", "safe, off the road", "no spare")
    assert out["summary"].startswith("Sarah Mitchell, Plus member M-20481537.")
    other = api.h.handler({"Details": {"Parameters": {"op": "intake"}, "ContactData": {"ContactId": "c-other"}}}, None)
    assert other["found"] == "false"
    call(api, "lookupmember", {"member_id": SARAH, "contact_id": "not-a-uuid"})  # model-supplied ids are ignored
    assert len(api.db.scan("IntakeTable")) == 1
    assert call(api, "reset", {"member_id": SARAH})[1]["removed"] == 1 and api.db.scan("IntakeTable") == []


def test_customer_facing_text_has_no_em_dashes(api):
    src = (ROOT / "lambdas" / "api" / "handler.py").read_text()
    assert "—" not in src


def test_chat_flow_greets_and_uses_the_agentic_cx_block_shape_connect_accepts():
    """Same shape as the kit's working chat flows (checked against Connect on 2026-10-19): the Agentic CX block needs
    SpeechRecognitionConfiguration even for chat, and the flow greets first so the chat never waits on a silent agent.
    The intake lookup uses the same contact id reference as the context variable ACXD sends to the Lambda."""
    import re
    import yaml

    class Loader(yaml.SafeLoader):
        pass
    Loader.add_multi_constructor("!", lambda loader, suffix, node: {"!" + suffix: (
        loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else
        loader.construct_sequence(node, deep=True) if isinstance(node, yaml.SequenceNode) else
        loader.construct_mapping(node, deep=True))})
    t = yaml.load((ROOT / "template.yaml").read_text(encoding="utf-8"), Loader=Loader)
    body = t["Resources"]["ChatFlow"]["Properties"]["Content"]["!Sub"][0]
    doc = json.loads(re.sub(r"\$\{[A-Za-z0-9.]+\}", "1", body))
    acts = {a["Identifier"]: a for a in doc["Actions"]}
    agent = next(a for a in doc["Actions"] if a["Type"] == "ConnectParticipantWithAgenticCX")["Parameters"]
    assert agent["SpeechRecognitionConfiguration"] == {"SpeechRecognitionEngine": "AMAZON_AGENTIC_VOICE"}
    assert agent["AudioFillerConfiguration"] == {"Enabled": False}
    assert agent["AgentConfiguration"]["ContextVariables"]["contact_id"] == "$.InitialContactId"
    step, seen = doc["StartAction"], []
    while acts[step]["Type"] != "ConnectParticipantWithAgenticCX":
        seen.append(acts[step]["Type"])
        step = acts[step]["Transitions"]["NextAction"]
    assert "MessageParticipant" in seen
    assert acts["intake"]["Parameters"]["LambdaInvocationAttributes"]["contact_id"] == "$.InitialContactId"
    for a in doc["Actions"]:
        tr = a["Transitions"]
        targets = {tr.get("NextAction")} | {c["NextAction"] for c in tr.get("Conditions", [])} | {e["NextAction"] for e in tr.get("Errors", [])}
        assert targets - {None} <= set(acts), a["Identifier"]
