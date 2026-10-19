"""Offline tests for the demo kit's web test pages: start-chat Lambda, page renderer, demo templates, shared stack."""
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

import pytest
import yaml

KIT = Path(__file__).resolve().parents[1]
DEMOS = KIT.parent
WEB = KIT / "web"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def web_demos():
    return sorted(p.parent.name for p in DEMOS.glob("*/demo.json") if "web" in json.loads(p.read_text(encoding="utf-8")))


# ------------------------------------------------------------------ start-chat Lambda
class FakeSsm:
    def __init__(self, params):
        self.params, self.reads = params, 0

    def get_parameter(self, Name):
        self.reads += 1
        if Name not in self.params:
            raise type("ParameterNotFound", (Exception,), {})()
        return {"Parameter": {"Value": self.params[Name]}}


class FakeConnect:
    def __init__(self):
        self.calls = []

    def start_chat_contact(self, **kw):
        self.calls.append(kw)
        return {"ContactId": "c-1", "ParticipantId": "p-1", "ParticipantToken": "t-1"}


@pytest.fixture()
def chat():
    h = _load("start_chat_handler", WEB / "start_chat" / "handler.py")
    h.SSM = FakeSsm({"/demo-kit/web/renault-rental/chat": json.dumps({"instance_id": "inst-1", "flow_id": "flow-1"}),
                     "/demo-kit/web/broken/chat": "{not json"})
    h.CONNECT = FakeConnect()
    return h


def post(h, path, body=None, method="POST"):
    ev = {"rawPath": path, "requestContext": {"http": {"method": method}}, "body": json.dumps(body if body is not None else {})}
    r = h.handler(ev, None)
    return r["statusCode"], json.loads(r["body"])


def test_start_chat_routing_and_methods(chat):
    for bad in ("/", "/start-chat", "/renault-rental", "/BAD_NAME/start-chat", "/a/b/start-chat", "/x/start-chat/extra"):
        assert post(chat, bad)[0] == 404, bad
    assert post(chat, "/renault-rental/start-chat", method="GET")[0] == 405
    assert post(chat, "/renault-rental/start-chat", method="OPTIONS")[0] == 405


def test_start_chat_not_ready(chat):
    code, body = post(chat, "/unknown-demo/start-chat")
    assert code == 503 and body["error"] == "chat_not_ready"
    assert post(chat, "/broken/start-chat")[0] == 503          # unreadable config is also "not ready"
    assert chat.CONNECT.calls == []


def test_start_chat_ok_and_locked_down(chat):
    code, body = post(chat, "/renault-rental/start-chat", {
        "InstanceId": "evil-instance", "ContactFlowId": "evil-flow",              # ignored: fixed server-side
        "ParticipantDetails": {"DisplayName": "<b>Bob</b> O'Neil; DROP"},
        "Attributes": {"x": "y"},
        "SupportedMessagingContentTypes": ["application/x-evil", "text/markdown"]})
    assert code == 200
    r = body["data"]["startChatResult"]
    assert r == {"ContactId": "c-1", "ParticipantId": "p-1", "ParticipantToken": "t-1"}
    call = chat.CONNECT.calls[0]
    assert call["InstanceId"] == "inst-1" and call["ContactFlowId"] == "flow-1"
    assert call["Attributes"] == {"demo_entry": "renault-rental"}
    assert call["ParticipantDetails"]["DisplayName"] == "bBobb O'Neil DROP"
    assert call["SupportedMessagingContentTypes"] == ["text/plain", "text/markdown"]


def test_start_chat_defaults_and_bad_body(chat):
    ev = {"rawPath": "/renault-rental/start-chat", "requestContext": {"http": {"method": "POST"}}, "body": "not json"}
    assert chat.handler(ev, None)["statusCode"] == 200
    call = chat.CONNECT.calls[0]
    assert call["ParticipantDetails"]["DisplayName"] == "Customer"
    assert "text/plain" in call["SupportedMessagingContentTypes"]


def test_start_chat_caches_the_demo_config(chat):
    post(chat, "/renault-rental/start-chat")
    post(chat, "/renault-rental/start-chat")
    assert chat.SSM.reads == 1
    chat._CACHE["renault-rental"] = (time.time() - 1, chat._CACHE["renault-rental"][1])   # expired
    post(chat, "/renault-rental/start-chat")
    assert chat.SSM.reads == 2


# ------------------------------------------------------------------ page renderer
@pytest.fixture()
def render():
    return _load("kit_render", WEB / "render.py")


@pytest.mark.parametrize("demo", web_demos())
def test_every_web_demo_renders(render, demo):
    page = render.render(demo, env={})
    cfg = json.loads(re.search(r"const CFG = (\{.*?\});\n", page, re.S).group(1))
    assert cfg["demo"] == demo and cfg["chatReady"] is False and cfg["liveSync"] is None
    assert '<meta name="robots" content="noindex,nofollow">' in page
    assert "/start-chat" in page and "@amazon-connect-touchpoint/web@" in page and 'integrity="sha384-' in page
    assert "__" not in re.sub(r"__proto__", "", page).replace("amazonConnectTouchpoint", "")   # no unreplaced tokens
    ready = render.render(demo, env={"WEB_CHAT_READY": "1"})
    assert '"chatReady": true' in ready


def test_live_sync_only_with_both_keys_and_never_leaks_half(render):
    demo = web_demos()[0]
    both = render.render(demo, env={"ACXD_KEYS_JSON": json.dumps({"deploymentKey": "DKEY123", "apiKey": "AKEY456"})})
    assert "DKEY123" in both and "AKEY456" in both and '"liveSync": {' in both
    half = render.render(demo, env={"ACXD_KEYS_JSON": json.dumps({"deploymentKey": "DKEY123"})})
    assert "DKEY123" not in half and '"liveSync": null' in half
    for junk in ("", "None", "{oops", "[]", "null"):
        assert '"liveSync": null' in render.render(demo, env={"ACXD_KEYS_JSON": junk})


def test_html_and_script_injection_are_neutralised(render, tmp_path, monkeypatch):
    d = tmp_path / "evil"
    (d / "web").mkdir(parents=True)
    (d / "demo.json").write_text(json.dumps({
        "name": "evil", "region": "ca-central-1",
        "web": {"title": "</script><script>alert(1)</script>", "brand": "<b>x</b>", "blurb": "__ACTIONS__ __CONFIG__",
                "prompts": ["<img src=x onerror=alert(1)>"], "assistant_name": "</script>", "connect_instance_id": "i"}}))
    (d / "web" / "actions.js").write_text("touchpoint.sendContext({actions: []});\n")
    monkeypatch.setattr(render, "DEMOS", tmp_path)
    page = render.render("evil", env={"WEB_CHAT_READY": "1"})
    assert "<script>alert(1)" not in page and "<img src=x" not in page and "<b>x</b>" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page
    assert page.count("</script>") == 2                      # only the template's own two script blocks
    assert "__ACTIONS__ __CONFIG__" in page                  # tokens in config text are not re-substituted
    assert "touchpoint.sendContext({actions: []});" in page  # per-demo actions.js is inserted


def test_stack_params(render, tmp_path, monkeypatch):
    assert render.stack_params(web_demos()[0])[0].startswith("ConnectInstanceId=")
    d = tmp_path / "bare"
    d.mkdir()
    (d / "demo.json").write_text(json.dumps({"name": "bare", "region": "r", "web": {"title": "t"}}))
    monkeypatch.setattr(render, "DEMOS", tmp_path)
    assert render.stack_params("bare") == []


# ------------------------------------------------------------------ demo templates and config
class _Loader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {"!" + suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {"!" + suffix: loader.construct_sequence(node, deep=True)}
    return {"!" + suffix: loader.construct_mapping(node, deep=True)}


_Loader.add_multi_constructor("!", _tag)


@pytest.mark.parametrize("demo", web_demos())
def test_demo_config_and_secret_hygiene(demo):
    cfg = json.loads((DEMOS / demo / "demo.json").read_text(encoding="utf-8"))
    web = cfg["web"]
    assert UUID.match(web["connect_instance_id"]) and UUID.match(web["acxd"]["workspace_id"]) and UUID.match(web["acxd"]["application_id"])
    flat = json.dumps(cfg).lower()
    assert "apikey" not in flat and "deploymentkey" not in flat      # keys live in Secrets Manager, never in the repo


@pytest.mark.parametrize("demo", web_demos())
def test_chat_flow_in_template(demo):
    t = yaml.load((DEMOS / demo / "template.yaml").read_text(encoding="utf-8"), Loader=_Loader)
    assert {"ConnectInstanceId", "AcxdWorkspaceId", "AcxdApplicationId", "AcxdAlias"} <= set(t["Parameters"])
    assert t["Parameters"]["AcxdAlias"].get("NoEcho") is True and "HasChat" in t["Conditions"]
    flow = t["Resources"]["ChatFlow"]
    assert flow["Condition"] == "HasChat" and flow["Properties"]["Type"] == "CONTACT_FLOW"
    assert t["Outputs"]["ChatFlowId"]["Condition"] == "HasChat"
    sub = flow["Properties"]["Content"]["!Sub"]
    text = re.sub(r"\$\{(\w+)\}", lambda m: {"AcxdWorkspaceId": "ws", "AcxdApplicationId": "app", "AcxdAlias": "key"}[m.group(1)], sub)
    doc = json.loads(text)
    ids = {a["Identifier"] for a in doc["Actions"]}
    assert doc["StartAction"] in ids
    for a in doc["Actions"]:
        tr = a["Transitions"]
        targets = {tr.get("NextAction")} | {c["NextAction"] for c in tr.get("Conditions", [])} | {e["NextAction"] for e in tr.get("Errors", [])}
        assert targets - {None} <= ids, (a["Identifier"], targets - ids)
    agent = next(a for a in doc["Actions"] if a["Type"] == "ConnectParticipantWithAgenticCX")["Parameters"]["AgentConfiguration"]
    assert agent["WorkspaceId"] == "ws" and agent["ApplicationId"] == "app" and agent["Alias"] == "key"
    assert agent["ContextVariables"]["channel"] == "CHAT"
    assert agent["ContextVariables"]["contact_id"] == "$.InitialContactId"      # same reference as the working voice flow
    params = next(a for a in doc["Actions"] if a["Type"] == "ConnectParticipantWithAgenticCX")["Parameters"]
    # Connect rejects the block (InvalidContactFlowException, Actions[n].Parameters) without SpeechRecognitionConfiguration,
    # even in a chat flow; checked against the real Connect instance on 2026-10-19 with demos/_kit/web/check_chat_flow.py
    assert params["SpeechRecognitionConfiguration"] == {"SpeechRecognitionEngine": "AMAZON_AGENTIC_VOICE"}
    assert params["AudioFillerConfiguration"] == {"Enabled": False}


def test_shared_stack_keeps_pages_private_and_unindexed():
    t = yaml.load((WEB / "stack.yaml").read_text(encoding="utf-8"), Loader=_Loader)
    r = t["Resources"]
    assert all(v is True for v in r["WebBucket"]["Properties"]["PublicAccessBlockConfiguration"].values())
    cfg = r["Distribution"]["Properties"]["DistributionConfig"]
    assert cfg["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    assert all(b["ViewerProtocolPolicy"] in ("https-only", "redirect-to-https") for b in cfg["CacheBehaviors"])
    headers = r["HeadersPolicy"]["Properties"]["ResponseHeadersPolicyConfig"]["CustomHeadersConfig"]["Items"]
    assert any(h["Header"] == "X-Robots-Tag" and "noindex" in h["Value"] for h in headers)
    assert cfg["CacheBehaviors"][0]["PathPattern"] == "/*/start-chat"
    policy = r["StartChatRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    assert all(s["Resource"] != "*" for s in policy)             # every IAM statement names explicit resources
