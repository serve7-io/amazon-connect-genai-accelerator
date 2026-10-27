"""Tests for the start-voice route (Touchpoint Voice / Voice mini -> StartWebRTCContact) and the voice flow shape."""
import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

KIT = Path(__file__).resolve().parents[1]
DEMOS = KIT.parent


def _load(path):
    spec = importlib.util.spec_from_file_location("voice_handler", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeSsm:
    def __init__(self, params):
        self.params, self.reads = params, []

    def get_parameter(self, Name):
        self.reads.append(Name)
        if Name not in self.params:
            raise type("ParameterNotFound", (Exception,), {})()
        return {"Parameter": {"Value": self.params[Name]}}


class FakeConnect:
    def __init__(self):
        self.calls, self.chat_calls = [], []

    def start_web_rtc_contact(self, **kw):
        self.calls.append(kw)
        return {"ConnectionData": {"Meeting": {"MeetingId": "m-1", "MediaRegion": "ca-central-1"},
                                   "Attendee": {"AttendeeId": "a-1", "JoinToken": "jt"}},
                "ContactId": "c-1", "ParticipantId": "p-1", "ParticipantToken": "t-1"}

    def start_chat_contact(self, **kw):
        self.chat_calls.append(kw)
        return {"ContactId": "c-2", "ParticipantId": "p-2", "ParticipantToken": "t-2"}


@pytest.fixture()
def h():
    mod = _load(KIT / "web" / "start_chat" / "handler.py")
    mod.SSM = FakeSsm({"/demo-kit/web/renault-rental/voice": json.dumps({"instance_id": "inst-1", "flow_id": "vflow-1"}),
                       "/demo-kit/web/renault-rental/chat": json.dumps({"instance_id": "inst-1", "flow_id": "cflow-1"}),
                       "/demo-kit/web/chat-only/chat": json.dumps({"instance_id": "inst-1", "flow_id": "cflow-2"})})
    mod.CONNECT = FakeConnect()
    return mod


def post(h, path, body=None, method="POST", text_plain=False):
    raw = json.dumps(body if body is not None else {})
    ev = {"rawPath": path, "requestContext": {"http": {"method": method}}, "body": raw,
          "headers": {"content-type": "text/plain" if text_plain else "application/json"}}
    r = h.handler(ev, None)
    return r["statusCode"], json.loads(r["body"])


def test_start_voice_returns_the_connection_data_touchpoint_expects(h):
    # Touchpoint posts JSON with Content-Type text/plain
    code, body = post(h, "/renault-rental/start-voice",
                      {"InstanceId": "evil", "ContactFlowId": "evil", "ParticipantDetails": {"DisplayName": "<i>Ana</i>"}}, text_plain=True)
    assert code == 200
    r = body["data"]["startWebRtcResult"]
    assert r["ConnectionData"]["Meeting"]["MeetingId"] == "m-1" and r["ConnectionData"]["Attendee"]["JoinToken"] == "jt"
    assert (r["ContactId"], r["ParticipantId"], r["ParticipantToken"]) == ("c-1", "p-1", "t-1")
    call = h.CONNECT.calls[0]
    assert call["InstanceId"] == "inst-1" and call["ContactFlowId"] == "vflow-1"     # fixed server-side, browser values ignored
    assert call["ParticipantDetails"]["DisplayName"] == "iAnai" and call["Attributes"] == {"demo_entry": "renault-rental"}
    assert h.CONNECT.chat_calls == []


def test_voice_and_chat_use_their_own_flow_and_cache(h):
    post(h, "/renault-rental/start-chat")
    assert h.CONNECT.chat_calls[0]["ContactFlowId"] == "cflow-1"
    post(h, "/renault-rental/start-voice")
    post(h, "/renault-rental/start-voice")
    assert h.SSM.reads.count("/demo-kit/web/renault-rental/voice") == 1                 # cached
    assert h.SSM.reads.count("/demo-kit/web/renault-rental/chat") == 1


def test_voice_not_ready_and_wrong_method(h):
    code, body = post(h, "/chat-only/start-voice")                                        # chat wired, voice not
    assert code == 503 and body["error"] == "voice_not_ready" and h.CONNECT.calls == []
    assert post(h, "/renault-rental/start-voice", method="GET")[0] == 405
    assert post(h, "/renault-rental/start-videos")[0] == 404
    assert post(h, "/BAD_NAME/start-voice")[0] == 404


def _template(demo):
    class L(yaml.SafeLoader):
        pass
    L.add_multi_constructor("!", lambda loader, suffix, node: {"!" + suffix: loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode)
                                                               else loader.construct_sequence(node, deep=True) if isinstance(node, yaml.SequenceNode)
                                                               else loader.construct_mapping(node, deep=True)})
    return yaml.load((DEMOS / demo / "template.yaml").read_text(encoding="utf-8"), Loader=L)


def test_renault_voice_flow_shape():
    t = _template("renault-rental")
    flow = t["Resources"]["VoiceFlow"]
    assert flow["Condition"] == "HasChat" and flow["Properties"]["Type"] == "CONTACT_FLOW"
    assert t["Outputs"]["VoiceFlowId"]["Condition"] == "HasChat"
    doc = json.loads(re.sub(r"\$\{(\w+)\}", "x", flow["Properties"]["Content"]["!Sub"]))
    ids = {a["Identifier"] for a in doc["Actions"]}
    assert doc["StartAction"] in ids
    for a in doc["Actions"]:
        tr = a["Transitions"]
        targets = {tr.get("NextAction")} | {c["NextAction"] for c in tr.get("Conditions", [])} | {e["NextAction"] for e in tr.get("Errors", [])}
        assert targets - {None} <= ids, (a["Identifier"], targets - ids)
    agent = next(a for a in doc["Actions"] if a["Type"] == "ConnectParticipantWithAgenticCX")["Parameters"]
    assert agent["SpeechRecognitionConfiguration"] == {"SpeechRecognitionEngine": "AMAZON_AGENTIC_VOICE"}
    assert agent["AudioFillerConfiguration"] == {"Enabled": False}
    assert agent["AgentConfiguration"]["ContextVariables"]["contact_id"] == "$.InitialContactId"
    assert agent["AgentConfiguration"]["ContextVariables"]["channel"] == "VOICE"
    types = [a["Type"] for a in doc["Actions"]]
    assert "UpdateContactTextToSpeechVoice" in types and "DisconnectParticipant" in types


def test_shared_stack_routes_and_allows_voice():
    text = (KIT / "web" / "stack.yaml").read_text(encoding="utf-8")
    assert 'PathPattern: "/*/start-voice"' in text and "connect:StartWebRTCContact" in text
    assert "Resource: \"*\"" not in text.split("StartChatRole:")[1].split("StartChatFunction:")[0]


def test_page_offers_talk_mode_only_when_voice_is_wired():
    spec = importlib.util.spec_from_file_location("kit_render", KIT / "web" / "render.py")
    render = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render)
    demo = "renault-rental"
    off = render.render(demo, env={"WEB_CHAT_READY": "1"})
    on = render.render(demo, env={"WEB_CHAT_READY": "1", "WEB_VOICE_READY": "1"})
    assert '"voiceReady": false' in off and '"voiceReady": true' in on
    for page in (off, on):                                          # same script either way; the toggle is hidden by default
        assert 'id="mode" hidden' in page and "/start-voice" in page and "voiceMini" in page and "/start-chat" in page
    assert "__" not in on.replace("amazonConnectTouchpoint", "")
