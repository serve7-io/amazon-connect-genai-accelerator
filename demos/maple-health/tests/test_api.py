"""Offline tests for the Maple Health API: email code, session from nlx_context only, appointments, intake, inbox."""
import datetime as dt
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 10, 20)
CID = "5e60332c-5ab1-4af8-9f6a-8fe7399105e0"
OTHER = "11111111-2222-4333-8444-555555555555"
KEYS = {"ClinicsTable": "clinic_id", "SlotsTable": "slot_id", "PatientsTable": "patient_id",
        "AppointmentsTable": "appointment_id", "SessionsTable": "contact_id", "InboxTable": "msg_id"}


class FakeDb:
    def __init__(self, tables):
        self.t = tables

    def get(self, table, key):
        r = self.t[table].get(next(iter(key.values())))
        return json.loads(json.dumps(r, default=str)) if r else None

    def put(self, table, item):
        self.t[table][item[KEYS[table]]] = json.loads(json.dumps(item, default=str))

    def scan(self, table):
        return [dict(r) for r in self.t[table].values()]

    def query(self, table, attr, value, index=None):
        return [dict(r) for r in self.t[table].values() if r.get(attr) == value]


def _seed():
    data = json.loads((ROOT / "data" / "seed.json").read_text(encoding="utf-8"))
    tables = {}
    for name, rows in data["tables"].items():
        fixed = []
        for r in rows:
            out = {}
            for k, v in r.items():
                if isinstance(v, str) and k.endswith("_date") and v[:1] == "+":
                    v = (TODAY + dt.timedelta(days=int(v))).isoformat()
                out[k] = v
            fixed.append(out)
        tables[name] = {r[KEYS[name]]: r for r in fixed}
    return tables


@pytest.fixture()
def h(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("TODAY", TODAY.isoformat())
    sys.path.insert(0, str(ROOT / "lambdas" / "api"))
    sys.modules.pop("handler", None)
    import handler
    handler.DB = FakeDb(_seed())
    yield handler
    sys.path.remove(str(ROOT / "lambdas" / "api"))


def call(h, op, body=None, cid=CID, key="k"):
    b = dict(body or {})
    if cid:
        b["nlx_context"] = {"contact_id": cid, "channel": "CHAT"}
    r = h.handler({"rawPath": f"/{op}", "headers": {"x-api-key": key}, "body": json.dumps(b)}, None)
    return r["statusCode"], json.loads(r["body"])


def code_from_inbox(h):
    r = h.handler({"rawPath": "/inbox", "requestContext": {"http": {"method": "GET"}}}, None)
    msgs = json.loads(r["body"])["messages"]
    return re.search(r"\b(\d{6})\b", msgs[0]["body"]).group(1), msgs


def verify(h, email="daniel.okafor@example.com", cid=CID):
    call(h, "send_code", {"email": email}, cid=cid)
    code, _ = code_from_inbox(h)
    return call(h, "verify_code", {"code": code}, cid=cid)[1]


def test_key_required_and_health_open(h):
    assert call(h, "clinic_info", key="wrong")[0] == 401
    r = h.handler({"rawPath": "/health", "headers": {}, "body": "{}"}, None)
    assert json.loads(r["body"])["units"] == 3


def test_send_code_never_returns_the_code_and_is_generic(h):
    _, known = call(h, "send_code", {"email": "Daniel.Okafor@example.com"})
    _, unknown = call(h, "send_code", {"email": "nobody@example.com"}, cid=OTHER)
    assert known["sent"] and unknown["sent"] and "d***@example.com" in known["message"]
    code, msgs = code_from_inbox(h)
    assert len(msgs) == 1 and msgs[0]["to"] == "daniel.okafor@example.com"   # unknown address gets no email
    assert code not in json.dumps(known) and code not in json.dumps(h.DB.t["SessionsTable"])   # only a hash is stored


def test_full_verification_and_wrong_code_and_lockout(h):
    call(h, "send_code", {"email": "daniel.okafor@example.com"})
    code, _ = code_from_inbox(h)
    bad = "000000" if code != "000000" else "111111"
    _, r = call(h, "verify_code", {"code": bad})
    assert r["verified"] is False and r["tries_left"] == 4
    _, r = call(h, "verify_code", {"code": code[:3] + " " + code[3:]})
    assert r["verified"] is True and r["first_name"] == "Daniel"
    for _ in range(5):
        call(h, "verify_code", {"code": bad}, cid=OTHER)
    _, r = call(h, "send_code", {"email": "priya.sharma@example.com"}, cid=OTHER)
    code2, _ = code_from_inbox(h)
    for _ in range(5):
        call(h, "verify_code", {"code": "000000" if code2 != "000000" else "111111"}, cid=OTHER)
    _, r = call(h, "verify_code", {"code": code2}, cid=OTHER)
    assert r["verified"] is False and r["error"] == "locked"


def test_code_expires_and_send_limit(h, monkeypatch):
    call(h, "send_code", {"email": "daniel.okafor@example.com"})
    code, _ = code_from_inbox(h)
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=11)
    monkeypatch.setattr(h, "_now", lambda: later)
    assert call(h, "verify_code", {"code": code})[1]["error"] == "code_expired"
    for _ in range(2):
        call(h, "send_code", {"email": "daniel.okafor@example.com"})
    assert call(h, "send_code", {"email": "daniel.okafor@example.com"})[1]["error"] == "too_many_codes"


def test_session_only_from_nlx_context_never_from_the_model(h):
    verify(h)
    # a model that puts another chat's contact id in the body gets nothing
    _, r = call(h, "my_appointments", {"contact_id": CID, "_cid": CID}, cid=None)
    assert r["error"] == "not_verified"
    _, r = call(h, "send_code", {"email": "daniel.okafor@example.com", "_cid": CID}, cid=None)
    assert r["error"] == "no_session"


def test_code_is_bound_to_its_chat(h):
    call(h, "send_code", {"email": "daniel.okafor@example.com"})
    code, _ = code_from_inbox(h)
    call(h, "send_code", {"email": "priya.sharma@example.com"}, cid=OTHER)
    assert call(h, "verify_code", {"code": code}, cid=OTHER)[1]["verified"] is False


def test_unverified_cannot_see_or_change_appointments(h):
    for op, body in (("my_appointments", {}), ("book", {"slot_id": "x"}), ("cancel", {"appointment_id": "MH-20417"})):
        assert call(h, op, body)[1]["error"] == "not_verified"


def test_clinic_info_and_slots_without_verification_bilingual(h):
    _, r = call(h, "clinic_info", {"language": "fr"})
    assert {c["clinic_id"] for c in r["clinics"]} == {"OTT", "KAN", "GAT"} and r["today"] == TODAY.isoformat()
    assert any("Maple Santé" in c["name"] for c in r["clinics"])
    _, r = call(h, "find_slots", {"service": "physiotherapy", "language_needed": "fr"})
    assert r["slots"] and all(s["clinic_id"] == "GAT" for s in r["slots"])
    _, r = call(h, "find_slots", {"service": "dentist"})
    assert r["error"] == "unknown_service" and "flu_vaccine" in r["valid_options"]
    _, r = call(h, "find_slots", {"service": "family_doctor", "clinic_id": "OTT"})
    dates = [(s["date"], s["time"]) for s in r["slots"]]
    assert dates == sorted(dates) and all(s["date"] > TODAY.isoformat() for s in r["slots"])
    taken = [s for s in h.DB.t["SlotsTable"].values() if s["status"] != "open"]
    assert not {s["slot_id"] for s in taken} & {s["slot_id"] for s in r["slots"]}


def test_book_reschedule_cancel(h):
    verify(h)
    _, mine = call(h, "my_appointments")
    assert [a["appointment_id"] for a in mine["appointments"]] == ["MH-20417"]
    _, slots = call(h, "find_slots", {"service": "flu_vaccine", "clinic_id": "OTT"})
    target = slots["slots"][0]["slot_id"]
    _, r = call(h, "book", {"slot_id": target, "replaces_appointment_id": "MH-20417"})
    assert r["booked"] and r["rescheduled_from"] == "MH-20417"
    assert h.DB.t["AppointmentsTable"]["MH-20417"]["status"] == "rescheduled"
    assert h.DB.t["SlotsTable"][h.DB.t["AppointmentsTable"]["MH-20417"]["slot_id"]]["status"] == "open"
    assert call(h, "book", {"slot_id": target})[1]["error"] == "slot_taken"
    new_id = r["appointment_id"]
    _, r = call(h, "cancel", {"appointment_id": new_id})
    assert r["cancelled"] and h.DB.t["SlotsTable"][target]["status"] == "open"


def test_cannot_touch_another_patients_appointment(h):
    verify(h)   # Daniel
    assert call(h, "cancel", {"appointment_id": "MH-20418"})[1]["error"] == "appointment_not_found"   # Priya's
    _, slots = call(h, "find_slots", {"service": "family_doctor"})
    r = call(h, "book", {"slot_id": slots["slots"][0]["slot_id"], "replaces_appointment_id": "MH-20418"})[1]
    assert r["error"] == "appointment_not_found"


def test_intake_for_the_connect_flow(h):
    verify(h)
    _, slots = call(h, "find_slots", {"service": "blood_test"})
    call(h, "book", {"slot_id": slots["slots"][0]["slot_id"]})
    out = h.handler({"Details": {"Parameters": {"op": "intake", "contact_id": CID}, "ContactData": {"ContactId": CID}}}, None)
    assert out["verified"] == "Yes (email code)" and out["patient_name"] == "Daniel Okafor" and out["patient_id"] == "MH-P-1002"
    assert "booked blood_test" in out["summary"] and out["next_appointment"]
    assert all(isinstance(v, str) for v in out.values())
    out = h.handler({"Details": {"Parameters": {"op": "intake", "contact_id": OTHER}}}, None)
    assert out["verified"] == "No" and out["found"] == "false"


def test_inbox_only_recent_demo_addresses(h, monkeypatch):
    h.DB.t["InboxTable"]["x"] = {"msg_id": "x", "to": "real.person@gmail.com", "subject": "s", "body": "b",
                                 "sent_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    call(h, "send_code", {"email": "amelie.tremblay@example.com"})
    _, msgs = code_from_inbox(h)
    assert [m["to"] for m in msgs] == ["amelie.tremblay@example.com"] and "Votre code" in msgs[0]["subject"]
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    monkeypatch.setattr(h, "_now", lambda: later)
    r = h.handler({"rawPath": "/inbox", "requestContext": {"http": {"method": "GET"}}}, None)
    assert json.loads(r["body"])["messages"] == []


def test_seed_is_synthetic():
    data = json.loads((ROOT / "data" / "seed.json").read_text(encoding="utf-8"))
    assert all(p["email"].endswith("@example.com") and p["demo_value"] for p in data["tables"]["PatientsTable"])
    assert all("555" in c["phone"] for c in data["tables"]["ClinicsTable"])
