"""Offline tests for the Maple Health page, ACXD files and Connect chat flow."""
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
KIT_WEB = ROOT.parent / "_kit" / "web"
ACTIONS = (ROOT / "web" / "actions.js").read_text(encoding="utf-8")
BODY = (ROOT / "web" / "body.html").read_text(encoding="utf-8")
FLOW = json.loads((ROOT / "acxd" / "mhpatientassistant.flow.json").read_text(encoding="utf-8"))
SEED = json.loads((ROOT / "data" / "seed.json").read_text(encoding="utf-8"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gj():
    return next(n for n in FLOW["nodes"].values() if n["type"] == "generative_journey")["metadata"]["generativeJourney"]


def test_page_lists_exactly_the_seed_patients():
    emails = {p["email"] for p in SEED["tables"]["PatientsTable"]}
    assert set(re.findall(r"[\w.]+@example\.com", BODY)) == emails


def test_inbox_script_is_text_only_and_needs_an_https_url():
    assert "innerHTML" not in ACTIONS and "outerHTML" not in ACTIONS and "eval(" not in ACTIONS
    assert "https:" in ACTIONS and "CFG.page" in ACTIONS
    ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', BODY))
    assert set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', ACTIONS)) <= ids


def test_flow_tools_match_the_data_requests_and_the_api():
    dr = {json.loads(p.read_text())["dataRequestId"]: json.loads(p.read_text())
          for p in (ROOT / "acxd" / "datarequests").glob("*.json")}
    tools = [t["dataRequest"]["dataRequestId"] for t in _gj()["tools"]]
    assert sorted(tools) == sorted(dr)
    handler = (ROOT / "lambdas" / "api" / "handler.py").read_text()
    for d in dr.values():
        op = d["webhook"]["environments"]["production"]["url"].rsplit("/", 1)[1]
        assert f"def {op}(" in handler and d["webhook"]["sendContext"] is True   # sendContext delivers the contact id
        assert any(h["value"].endswith(":NLX.Secret}") for h in d["webhook"]["environments"]["production"]["headers"])
    for name in tools:
        assert name in _gj()["prompt"], f"the prompt must name {name}"


def test_flow_shape_and_model():
    g = _gj()
    assert g["modelType"] == "anthropic.claude-sonnet-5" and g["temperature"] == 0.2
    assert [e["name"] for e in g["exitConditions"]] == ["agent", "done"]
    assert "exits are silent" in g["prompt"] and "never greet again" in g["prompt"].lower()
    assert "911" in g["prompt"] and "medical advice" in g["prompt"]
    ids = set(FLOW["nodes"])
    for n in FLOW["nodes"].values():
        for ch in n["childNodes"]:
            assert ch["nodeId"] in ids
    esc = next(n for n in FLOW["nodes"].values() if n["type"] == "escalate")
    assert esc["messages"][0]["body"]


def test_rendered_page(monkeypatch):
    render = _load("kit_render", KIT_WEB / "render.py")
    page = render.render("maple-health", env={"WEB_CHAT_READY": "1"})
    assert 'id="inbox-list"' in page and "mh-grid" in page and "__EXTRA" not in page
    cfg = json.loads(re.search(r"const CFG = (\{.*?\});\n", page, re.S).group(1))
    assert isinstance(cfg["page"], dict)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_rendered_inline_script_is_valid_javascript(tmp_path):
    render = _load("kit_render", KIT_WEB / "render.py")
    page = render.render("maple-health", env={"WEB_CHAT_READY": "1"})
    script = re.findall(r"<script(?![^>]*src)[^>]*>(.*?)</script>", page, re.S)[0]
    f = tmp_path / "inline.js"
    f.write_text(script, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


class _Loader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {"!" + suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {"!" + suffix: loader.construct_sequence(node, deep=True)}
    return {"!" + suffix: loader.construct_mapping(node, deep=True)}


_Loader.add_multi_constructor("!", _tag)


def test_chat_flow_greets_then_hands_over_with_the_session_record():
    t = yaml.load((ROOT / "template.yaml").read_text(encoding="utf-8"), Loader=_Loader)
    doc = json.loads(re.sub(r"\$\{([\w.]+)\}", "x", t["Resources"]["ChatFlow"]["Properties"]["Content"]["!Sub"]))
    acts = {a["Identifier"]: a for a in doc["Actions"]}
    assert doc["StartAction"] == "log" and acts["log"]["Transitions"]["NextAction"] == "guide"
    assert acts["guide"]["Transitions"]["NextAction"] == "greet" and acts["greet"]["Transitions"]["NextAction"] == "agent"
    esc = next(c["NextAction"] for c in acts["agent"]["Transitions"]["Conditions"] if c["Condition"]["Operands"] == ["Escalation"])
    assert esc == "intake"
    assert acts["intake"]["Parameters"]["LambdaInvocationAttributes"] == {"op": "intake", "contact_id": "$.InitialContactId"}
    assert acts["attrs"]["Transitions"]["NextAction"] == "queue" and acts["queue"]["Transitions"]["NextAction"] == "transfer"
    assert not any(a["Type"] == "UpdateContactRoutingBehavior" for a in doc["Actions"])   # rejected by Connect (see kit notes)
    guide = t["Resources"]["GuideFlow"]["Properties"]["Content"]["!Sub"][0]
    attrs = set(re.findall(r"\$\.Attributes\.(\w+)", guide))
    assert attrs <= set(acts["attrs"]["Parameters"]["Attributes"])
