"""Offline tests for the ACXD API operations added for the ACXD -> secure keypad -> ACXD round trip."""
import json, sys, types
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def api(monkeypatch):
    payments, handoffs = {}, {}
    common = types.ModuleType("common")
    common.time = __import__("time")
    import ast  # pure helpers straight from common.py (no boto3 needed)
    src = (ROOT / "lambdas" / "common.py").read_text(); tree = ast.parse(src)
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in
            ("next_renewal", "month_year", "money_spoken")) or (isinstance(n, ast.Assign) and
            n.targets[0].id in ("LINE_NAMES", "MONTHS"))]
    common.Decimal = __import__("decimal").Decimal
    exec(compile(ast.Module(body=keep, type_ignores=[]), "common_helpers", "exec"), common.__dict__)
    members = {"M-60425183": {"member_id": "M-60425183", "name": "Thomas Beaulieu", "tier": "Plus", "dob": "1975-06-12",
                              "postal_code": "K1A 0B1", "phone": "+16135550114", "sms_capable": True,
                              "vehicle": "2022 Honda CR-V"}}
    common.member = lambda mid: members.get(mid)
    members["M-20481537"] = {"member_id": "M-20481537", "name": "Sarah Mitchell", "tier": "Plus", "phone": "+16135550152",
                             "dob": "1986-04-22", "postal_code": "K7L 3N6", "vehicle": "2023 Honda CR-V",
                             "vehicle_plate": "ON ABCD 123", "member_since": "2019", "open_cases": 0,
                             "service_history": [{"date": "2025-03", "service": "Battery boost"}]}
    common.T_MEMBERS = types.SimpleNamespace(scan=lambda **kw: {"Items": list(members.values())})
    common.now_iso = lambda: "2026-10-14T00:00:00+00:00"
    pol = {"policy_id": "AU-88214471", "member_id": "M-1001", "plan_type": "full_pay", "amount_due": "1284.60",
           "currency": "CAD", "status": "renewal_due", "line": "auto", "renewal_date": "2026-10-27"}
    common.policy = lambda pid: dict(pol) if pid == pol["policy_id"] else None
    common.idempotency_key = lambda *a: "|".join(map(str, a))
    common.keypad_attempt_keys = lambda cid, pid, amt: [f"{cid}#{n}|{pid}|{amt}" for n in (1, 2, 3)]
    common.existing_payment = lambda k: payments.get(k)
    common.T_HANDOFF = types.SimpleNamespace(put_item=lambda Item: handoffs.__setitem__(Item["contact_id"], Item))
    monkeypatch.setitem(sys.modules, "common", common)
    monkeypatch.setenv("API_KEY", "k")
    sys.path.insert(0, str(ROOT / "lambdas" / "acxd_api")); sys.modules.pop("handler", None)
    import handler
    yield types.SimpleNamespace(h=handler, payments=payments, handoffs=handoffs, pol=pol)
    sys.path.remove(str(ROOT / "lambdas" / "acxd_api"))


def call(api, op, body):
    r = api.h.handler({"headers": {"x-api-key": "k"}, "rawPath": "/" + op, "body": json.dumps(body)}, None)
    return r["statusCode"], json.loads(r["body"])


def test_prepare_card_entry_records_handoff_without_card_data(api):
    code, out = call(api, "prepare_card_entry", {"contact_id": "c-1", "policy_id": "AU-88214471", "member_id": "M-1001"})
    assert code == 200 and out == {"ready": True, "amount": "1284.60", "currency": "CAD"}
    assert api.handoffs["c-1"]["reason"] == "collect_new_card" and api.handoffs["c-1"]["policy_id"] == "AU-88214471"
    code, out = call(api, "prepare_card_entry", {"contact_id": "c-1", "policy_id": "AU-88214471", "member_id": "M-9"})
    assert out == {"ready": False, "reason": "not_payable"}


def test_payment_status_sees_keypad_attempts(api):
    assert call(api, "payment_status", {"contact_id": "c-1", "policy_id": "AU-88214471"})[1]["result"] == "none"
    api.payments["c-1#1|AU-88214471|1284.60"] = {"result": "declined", "confirmation": ""}
    api.payments["c-1#2|AU-88214471|1284.60"] = {"result": "approved", "confirmation": "CCG-PAY-1", "brand": "Visa", "last4": "4242"}
    out = call(api, "payment_status", {"contact_id": "c-1", "policy_id": "AU-88214471"})[1]
    assert out == {"result": "approved", "confirmation": "CCG-PAY-1", "brand": "Visa", "last4": "4242", "attempts": 2,
                   "policy_id": "AU-88214471", "line_name": "auto insurance", "amount_spoken": "$1,284.60",
                   "next_renewal": "October 2027", "receipt": "none"}


def test_lookup_member_by_spoken_membership_number(api):
    assert call(api, "lookup_member", {"membership_number": "m 6042 5183"})[1] == {
        "found": True, "member_id": "M-60425183", "first_name": "Thomas", "tier": "Plus", "vehicle": "2022 Honda CR-V",
        "vehicle_plate": "", "member_since": "", "service_history": [], "open_cases": 0}
    assert call(api, "lookup_member", {"membership_number": "123"})[1] == {"found": False}


def test_lookup_member_by_phone_and_crm_fields(api):
    """Scenario 1: 'membership number or the phone number on your account'."""
    by_no = call(api, "lookup_member", {"membership_number": "M-20481537"})[1]
    assert by_no["first_name"] == "Sarah" and by_no["vehicle"] == "2023 Honda CR-V" and by_no["member_since"] == "2019"
    assert by_no["service_history"][0]["service"] == "Battery boost" and by_no["open_cases"] == 0
    assert call(api, "lookup_member", {"phone": "(613) 555-0152"})[1]["member_id"] == "M-20481537"
    assert call(api, "lookup_member", {"membership_number": "613 555 0152"})[1]["member_id"] == "M-20481537"
    assert call(api, "lookup_member", {"phone": "613 555 0199"})[1] == {"found": False}


def test_verify_service_level_by_vehicle(api):
    """Scenario 1 security level for service requests: membership number + vehicle confirmation."""
    ok = call(api, "verify_member", {"membership_number": "M-20481537", "interaction_type": "service",
                                     "vehicle": "yes"})[1]
    assert ok["verified"] and ok["level"] == "service"
    assert call(api, "verify_member", {"membership_number": "M-20481537", "interaction_type": "service",
                                       "vehicle": "a Ford F-150"})[1] == {"verified": False}


@pytest.mark.parametrize("body,ok", [
    ({"interaction_type": "payment", "dob": "June 12 1975", "postal_code": "k1a0b1"}, True),
    ({"interaction_type": "payment", "dob": "1975-06-12", "postal_code": "K1A 0B2"}, False),
    ({"interaction_type": "payment", "dob": "1975-06-13", "postal_code": "K1A 0B1"}, False),
    ({"interaction_type": "service", "vehicle": "yes it's the Honda"}, True),
    ({"interaction_type": "service", "vehicle": "a Ford"}, False),
    ({"interaction_type": "account_change", "postal_code": "K1A 0B1", "phone": "613-555-0114"}, True),
    ({"interaction_type": "account_change", "postal_code": "K1A 0B1", "phone": ""}, False),
    ({"interaction_type": "nonsense", "dob": "1975-06-12", "postal_code": "K1A 0B1"}, False),
])
def test_verify_member_security_levels(api, body, ok):
    out = call(api, "verify_member", {"membership_number": "M-60425183", **body})[1]
    assert out["verified"] is ok
    if not ok:
        assert out["verified"] is False and set(out) <= {"verified", "missing"}  # a wrong factor is never named


@pytest.mark.parametrize("dob,postal", [("12th of June, 1975", "K one A zero B one"), ("June 12th 1975", "k1a-0b1")])
def test_verify_member_spoken_formats(api, dob, postal):
    out = call(api, "verify_member", {"membership_number": "60425183", "interaction_type": "payment", "dob": dob, "postal_code": postal})[1]
    assert out["verified"] is True


def test_verify_member_asked_too_early_lists_missing(api):
    out = call(api, "verify_member", {"membership_number": "M-60425183", "interaction_type": "payment", "dob": "1975-06-12"})[1]
    assert out == {"verified": False, "missing": ["postal_code"]}


def test_contact_id_comes_from_acxd_context_not_the_model(api):
    cid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    r = api.h.handler({"headers": {"x-api-key": "k"}, "rawPath": "/prepare_card_entry",
                       "body": json.dumps({"contact_id": "{contact_id}", "policy_id": "AU-88214471", "member_id": "M-1001",
                                           "nlx_context": {"variables": {"contact_id": cid}}})}, None)
    assert json.loads(r["body"])["ready"] is True and cid in api.handoffs
