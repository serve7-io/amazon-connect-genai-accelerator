"""Offline tests for the Renault web page: fleet cards in sync with the seed, Live Sync actions consistent with the
renaultlivesync flow prompt, form labels, and the rendered page."""
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
FLOW = json.loads((ROOT / "acxd" / "renaultlivesync.flow.json").read_text(encoding="utf-8"))
SEED = json.loads((ROOT / "data" / "seed.json").read_text(encoding="utf-8"))
ACTION_NAMES = {"highlight_cars", "set_booking_details", "show_quote", "show_confirmation"}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ls_node():
    return next(n for n in FLOW["nodes"].values() if n["type"] == "multimodal")["metadata"]["multimodal"]


def test_body_html_is_generated_from_the_seed():
    mod = _load("make_body", ROOT / "web" / "make_body.py")
    assert BODY == mod.build(), "web/body.html is out of date: run python3 demos/renault-rental/web/make_body.py"


def test_one_card_per_model_and_model_ids_match_the_actions():
    model_ids = {r["model_id"] for r in SEED["tables"]["FleetTable"]}
    cards = set(re.findall(r'<article class="car" id="car-([a-z0-9]+)"', BODY))
    assert cards == model_ids and len(model_ids) == 10
    listed = set(re.findall(r'"([a-z0-9]+)"', re.search(r"const MODEL_IDS = \[(.*?)\]", ACTIONS, re.S).group(1)))
    assert listed == model_ids


def test_actions_registered_in_the_page_match_the_flow_prompt():
    registered = set(re.findall(r'^\s*action: "([a-z_]+)"', ACTIONS, re.M))
    assert registered == ACTION_NAMES
    prompt = _ls_node()["prompt"]
    for name in ACTION_NAMES:
        assert re.search(rf"^- {name}:", prompt, re.M), f"the flow prompt must explain {name}"


def test_elements_touched_by_the_handlers_exist():
    ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', BODY))
    used = set(re.findall(r'(?:byId|text|setField|reveal)\("([A-Za-z0-9_-]+)"', ACTIONS))
    assert used <= ids, used - ids


def test_booking_form_fields_have_labels():
    for field in ("model", "start_date", "end_date", "licence", "customer_name", "phone"):
        assert re.search(rf'<label for="{field}">', BODY), field
        assert re.search(rf'<(input|select)[^>]* id="{field}"', BODY), field


def test_handlers_never_use_innerhtml():
    assert "innerHTML" not in ACTIONS and "outerHTML" not in ACTIONS and "eval(" not in ACTIONS


def test_flow_is_a_live_sync_flow_with_the_rental_tools():
    ls = _ls_node()
    assert ls["type"] == "agentic" and ls["temperature"] == 0.2
    assert [t["dataRequest"]["dataRequestId"] for t in ls["tools"]] == [
        "rnlistcars", "rncheckavailability", "rnquote", "rncreatereservation", "rngetreservation", "rncancelreservation"]
    ids = set(FLOW["nodes"])
    for n in FLOW["nodes"].values():
        for ch in n["childNodes"]:
            assert ch["nodeId"] in ids


def test_application_attaches_the_live_sync_flow_as_welcome():
    app = json.loads((ROOT / "acxd" / "renault-car-rental.application.json").read_text(encoding="utf-8"))
    assert {f["flowId"] for f in app["flows"]} == {"renaultcarrental", "renaultlivesync"}
    assert app["settings"]["defaultFlows"]["welcome"]["flowId"] == "renaultlivesync"


def test_rendered_page_contains_the_extras():
    render = _load("kit_render", KIT_WEB / "render.py")
    page = render.render("renault-rental", env={"WEB_CHAT_READY": "1"})
    assert 'id="fleet-card"' in page and 'id="start_date"' in page and "highlight_cars" in page and ".car.hl" in page
    assert "__EXTRA" not in page and "__ACTIONS__" not in page
    assert page.index('id="fleet-card"') < page.index("<h2>Try asking</h2>")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_rendered_inline_script_is_valid_javascript(tmp_path):
    render = _load("kit_render", KIT_WEB / "render.py")
    page = render.render("renault-rental", env={"WEB_CHAT_READY": "1", "ACXD_KEYS_JSON": json.dumps({"deploymentKey": "d", "apiKey": "a"})})
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


def test_connect_chat_flow_greets_before_handing_over_to_the_live_sync_agent():
    """The Live Sync node does not speak first. The web chat shows 'Thinking...' and no input box until a first
    message arrives, so the Connect flow must send the greeting itself, before the Agentic CX block."""
    t = yaml.load((ROOT / "template.yaml").read_text(encoding="utf-8"), Loader=_Loader)
    sub = t["Resources"]["ChatFlow"]["Properties"]["Content"]["!Sub"]
    doc = json.loads(re.sub(r"\$\{(\w+)\}", "x", sub))
    actions = {a["Identifier"]: a for a in doc["Actions"]}
    agent = next(a["Identifier"] for a in doc["Actions"] if a["Type"] == "ConnectParticipantWithAgenticCX")
    step, seen = doc["StartAction"], []
    while step != agent:
        seen.append(step)
        step = actions[step]["Transitions"]["NextAction"]
    greet = [actions[i] for i in seen if actions[i]["Type"] == "MessageParticipant"]
    assert greet and "Zdravo!" in greet[0]["Parameters"]["Text"] and "How can I help?" in greet[0]["Parameters"]["Text"]
