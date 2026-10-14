"""Offline tests for the CAA Scenario 1 roadside API (fake tables)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CID = "1b2c3d4e-0000-4000-8000-00000000abcd"


class FakeTable:
    def __init__(self, key, rows=()):
        self.key, self.rows = key, {r[key]: dict(r) for r in rows}

    def get_item(self, Key):
        r = self.rows.get(Key[self.key])
        return {"Item": dict(r)} if r else {}

    def put_item(self, Item, **_):
        self.rows[Item[self.key]] = dict(Item)


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    sys.path.insert(0, str(ROOT / "lambdas" / "api")); sys.modules.pop("handler", None)
    import handler
    handler.CASES = FakeTable("contact_id")
    handler.MEMBERS = FakeTable("member_id", [
        {"member_id": "M-20481537", "name": "Sarah Mitchell", "tier": "Plus", "vehicle": "2023 Honda CR-V"},
        {"member_id": "M-70539261", "name": "Nadia Haddad", "tier": "Basic"}])
    yield handler
    sys.path.remove(str(ROOT / "lambdas" / "api"))


def call(h, op, body, key="k"):
    r = h.handler({"rawPath": f"/{op}", "headers": {"x-api-key": key}, "body": json.dumps(body),
                   "requestContext": {"http": {"method": "POST"}}}, None)
    return r["statusCode"], json.loads(r["body"])


def test_auth_health_and_page(api):
    assert call(api, "get_coverage", {}, key="bad")[0] == 401
    assert call(api, "health", {}, key="")[1]["ok"]
    page = api.handler({"rawPath": "/", "requestContext": {"http": {"method": "GET"}}}, None)
    assert page["statusCode"] == 200 and "DEMO" in page["body"] and "widget not configured" in page["body"]


def test_page_embeds_widget_when_configured(api, monkeypatch):
    monkeypatch.setenv("WIDGET_ID", "w-1"); monkeypatch.setenv("WIDGET_SNIPPET_ID", "snip-1")
    monkeypatch.setenv("WIDGET_SRC", "https://example.my.connect.aws/connectwidget/static/amazon-connect-chat-interface-client.js")
    body = api.handler({"rawPath": "/", "requestContext": {"http": {"method": "GET"}}}, None)["body"]
    assert "'w-1'" in body and "'snip-1'" in body and "amazon_connect('snippetId'" in body


def test_coverage_by_tier(api):
    plus = call(api, "get_coverage", {"member_id": "M-20481537"})[1]
    assert plus["tier"] == "Plus" and plus["tow_km"] == 200 and "200 km" in plus["tow_included_text"]
    assert call(api, "get_coverage", {"membership_number": "70539261"})[1]["tow_km"] == 10
    assert call(api, "get_coverage", {"member_id": "M-1"})[1] == {"found": False}


def test_case_from_acxd_uses_context_contact_id_and_reaches_connect(api):
    body = {"payload": {"contact_id": "made-up-by-model", "member_id": "M-20481537", "member_name": "Sarah Mitchell",
                        "tier": "Plus", "vehicle": "2023 Honda CR-V", "verified": "true",
                        "location": "Hwy 401 EB near Joyceville Rd, Kingston, ON", "service_type": "Flat tire / tow",
                        "safe": "yes", "spare": "No spare", "passengers": "2 children", "photo_received": "yes",
                        "concern": "Wants ETA; Sunday shop hours"},
            "nlx_context": {"contact_id": CID}}
    code, r = call(api, "create_roadside_case", body)
    assert code == 200 and r["created"] and r["case_id"].startswith("RSA-") and r["priority"] == "Standard"
    stored = api.CASES.rows[CID]
    assert stored["verified"] and stored["photo_received"] and stored["safe"]
    attrs = api.handler({"Details": {"ContactData": {"InitialContactId": CID, "ContactId": CID}}}, None)
    assert attrs["found"] == "true" and attrs["s1_case_id"] == r["case_id"]
    assert attrs["s1_verified"] == "Verified (service level)" and attrs["s1_photo"] == "Yes"
    assert "Joyceville" in attrs["s1_summary"] and all(isinstance(v, str) for v in attrs.values())


def test_unsafe_is_high_priority_and_missing_ids(api):
    body = {"member_id": "M-20481537", "safe": "no", "contact_id": CID}
    assert call(api, "create_roadside_case", body)[1]["priority"] == "High"
    assert call(api, "create_roadside_case", {"member_id": "M-20481537"})[1] == {"created": False, "reason": "no_contact_id"}
    assert api.handler({"Details": {"ContactData": {"InitialContactId": "x"}}}, None) == {"found": "false"}
