"""Offline tests: decryption of Connect-style ciphertext, Luhn, gateway rules, vault cleanup, no card data returned.
Run: python3 -m pytest -q tests"""
import base64, os, sys, types
from pathlib import Path
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]

class FakeTable:
    def __init__(self): self.items = {}
    def get_item(self, Key, **_): return {"Item": self.items.get(tuple(Key.values()))} if tuple(Key.values()) in self.items else {}
    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, **_):
        field = UpdateExpression.split()[1]
        row = self.items.setdefault(tuple(Key.values()), dict(Key)); row[field] = ExpressionAttributeValues[":v"]
    def delete_item(self, Key): self.items.pop(tuple(Key.values()), None)
    def scan(self, **_): return {"Items": list(self.items.values())}

@pytest.fixture()
def env(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    vault, notes, charges = FakeTable(), [], []
    common = types.ModuleType("common")
    common.ddb = types.SimpleNamespace(Table=lambda name: vault)
    common.policy = lambda pid: {"policy_id": pid, "member_id": "M-1001", "plan_type": "full_pay", "amount_due": "1284.60", "status": "renewal_due"} if pid == "AU-88214471" else None
    common.now_iso = lambda: "2026-10-12T00:00:00+00:00"
    common.idempotency_key = lambda *a: "|".join(map(str, a))
    def charge(**kw):
        charges.append(kw)
        return {"result": {"approve": "approved", "decline": "declined"}.get(kw["outcome_hint"], "gateway_unavailable"),
                "confirmation": "CCG-PAY-00001" if kw["outcome_hint"] == "approve" else "", "brand": kw["brand"], "last4": kw["last4"]}
    common.simulated_charge = charge
    common.T_HANDOFF = FakeTable()
    sms = []
    common.send_receipt_sms = lambda phone, pol, item: (sms.append((phone, item["confirmation"])), "sent")[1]
    common.money_spoken = lambda a: "$" + str(a)
    common.add_note = lambda *a: notes.append(a)
    monkeypatch.setitem(sys.modules, "common", common)
    monkeypatch.setenv("VAULT_TABLE", "v"); monkeypatch.setenv("ENCRYPTION_KEY_ID", "kid-1"); monkeypatch.setenv("PRIVATE_KEY_SECRET_ARN", "x")
    sys.path.insert(0, str(ROOT / "lambdas" / "payment")); sys.modules.pop("handler", None)
    import handler
    handler._provider = handler._make_provider(pem)
    # encrypt like Connect does: AWS Encryption SDK, raw RSA public key, provider "AmazonConnect"
    import aws_encryption_sdk
    from aws_encryption_sdk.identifiers import EncryptionKeyType, WrappingAlgorithm
    from aws_encryption_sdk.internal.crypto.wrapping_keys import WrappingKey
    from aws_encryption_sdk.key_providers.raw import RawMasterKeyProvider
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    class Pub(RawMasterKeyProvider):
        provider_id = "AmazonConnect"
        def _get_raw_key(self, key_id):
            return WrappingKey(wrapping_algorithm=WrappingAlgorithm.RSA_OAEP_SHA512_MGF1, wrapping_key=pub, wrapping_key_type=EncryptionKeyType.PUBLIC)
    p = Pub(); p.add_master_key("kid-1")
    client = aws_encryption_sdk.EncryptionSDKClient()
    enc = lambda s: base64.b64encode(client.encrypt(source=s.encode(), key_provider=p)[0]).decode()
    yield types.SimpleNamespace(h=handler, enc=enc, vault=vault, notes=notes, charges=charges, handoff=common.T_HANDOFF, sms=sms)
    sys.path.remove(str(ROOT / "lambdas" / "payment"))

def event(**params):
    return {"Details": {"Parameters": params, "ContactData": {"InitialContactId": "c-1",
                                                               "CustomerEndpoint": {"Address": "+16135550114", "Type": "TELEPHONE_NUMBER"}}}}

def enter(e, pan, exp="0928", cvv="123"):
    for f, v in (("pan", pan), ("exp", exp), ("cvv", cvv)):
        assert e.h.handler(event(action="stash", field=f, value=e.enc(v)), None) == {"result": "stashed"}

def test_approve_returns_only_safe_fields_and_clears_vault(env):
    enter(env, "4242424242424242")
    out = env.h.handler(event(action="charge", policyId="AU-88214471", memberId="M-1001"), None)
    assert out == {"result": "approved", "confirmation": "CCG-PAY-00001", "brand": "Visa", "last4": "4242",
                   "amount_spoken": "$1284.60", "confirmation_spoken": "C C G, P A Y, 0 0 0 0 1", "receipt": "sent"}
    assert env.vault.items == {} and len(env.notes) == 1 and "4242424242424242" not in str(env.notes)

def test_decline_and_gateway_error(env):
    enter(env, "4000000000000002")
    assert env.h.handler(event(action="charge", policyId="AU-88214471", memberId="M-1001"), None)["result"] == "declined"
    enter(env, "4000000000000119")
    assert env.h.handler(event(action="charge", policyId="AU-88214471", memberId="M-1001"), None)["result"] == "gateway_unavailable"

def test_bad_luhn_expired_and_timeout(env):
    enter(env, "4242424242424241")
    assert env.h.handler(event(action="charge", policyId="AU-88214471", memberId="M-1001"), None)["result"] == "invalid_card"
    enter(env, "4242424242424242", exp="0125")
    assert env.h.handler(event(action="charge", policyId="AU-88214471", memberId="M-1001"), None)["result"] == "card_expired"
    assert env.h.handler(event(action="stash", field="pan", value="Timeout"), None) == {"result": "input_timeout"}
    assert env.charges == []

def test_wrong_member_not_payable(env):
    enter(env, "4242424242424242")
    assert env.h.handler(event(action="charge", policyId="AU-88214471", memberId="M-9999"), None)["result"] == "not_payable"


def test_simulated_partner_fields(env, monkeypatch):
    monkeypatch.setattr(env.h, "PARTNER_MODE", "simulated_vendor")
    s = env.h.handler(event(action="start_session", policyId="AU-88214471"), None)
    assert s["result"] == "session_started" and s["partner_session_id"].startswith("PS-")
    enter(env, "4242424242424242")
    out = env.h.handler(event(action="charge", policyId="AU-88214471", memberId="M-1001"), None)
    assert out["partner_status"] == "AUTHORISED" and out["masked_pan"] == "************4242"
    assert out["partner_session_id"] == s["partner_session_id"] and "4242424242424242" not in str(out)


def test_retry_after_decline_uses_a_new_attempt_key(env):
    enter(env, "4000000000000002")
    assert env.h.handler(event(action="charge", policyId="AU-88214471", memberId="M-1001", attempt="1"), None)["result"] == "declined"
    enter(env, "4242424242424242")
    assert env.h.handler(event(action="charge", policyId="AU-88214471", memberId="M-1001", attempt="2"), None)["result"] == "approved"
    assert [k["key"] for k in env.charges] == ["c-1#1|AU-88214471|1284.60", "c-1#2|AU-88214471|1284.60"]


def test_paid_policy_is_never_charged_again(env, monkeypatch):
    monkeypatch.setattr(env.h.c, "policy", lambda pid: {"member_id": "M-1001", "plan_type": "full_pay", "amount_due": "1", "status": "paid"})
    enter(env, "4242424242424242")
    assert env.h.handler(event(action="charge", policyId="AU-88214471", memberId="M-1001", attempt="2"), None)["result"] == "already_paid"
    assert env.charges == []


def test_handoff_returns_prepared_card_entry_once(env):
    assert env.h.handler(event(action="handoff"), None) == {"reason": "agent"}
    env.handoff.items[("c-1",)] = {"contact_id": "c-1", "reason": "collect_new_card", "policy_id": "AU-88214471", "member_id": "M-1001"}
    assert env.h.handler(event(action="handoff"), None) == {"reason": "collect_new_card", "policyId": "AU-88214471", "memberId": "M-1001"}
    assert env.h.handler(event(action="handoff"), None) == {"reason": "agent"}


def test_handoff_falls_back_to_the_one_recent_preparation(env):
    import time
    env.handoff.items[("wrong-id",)] = {"contact_id": "wrong-id", "reason": "collect_new_card", "policy_id": "AU-88214471",
                                        "member_id": "M-1001", "expires_at": int(time.time()) + 3600}
    assert env.h.handler(event(action="handoff"), None)["reason"] == "collect_new_card"
    assert env.handoff.items == {}
    env.handoff.items[("old",)] = {"contact_id": "old", "reason": "collect_new_card", "policy_id": "AU-88214471",
                                   "member_id": "M-1001", "expires_at": int(time.time()) + 3000}
    assert env.h.handler(event(action="handoff"), None) == {"reason": "agent"}   # older than 2 minutes: not used


def test_receipt_sms_goes_to_caller_on_approval_only(env):
    enter(env, "4000000000000002")
    env.h.handler(event(action="charge", policyId="AU-88214471", memberId="M-1001", attempt="1"), None)
    assert env.sms == []
    enter(env, "4242424242424242")
    env.h.handler(event(action="charge", policyId="AU-88214471", memberId="M-1001", attempt="2"), None)
    assert env.sms == [("+16135550114", "CCG-PAY-00001")]


def test_receipt_text_matches_caa_script():
    import ast, decimal
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "lambdas" / "common.py").read_text()
    keep = [n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name in ("receipt_text", "month_year")
            or isinstance(n, ast.Assign) and n.targets[0].id in ("LINE_NAMES", "MONTHS")]
    ns = {"Decimal": decimal.Decimal}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "c", "exec"), ns)
    txt = ns["receipt_text"]({"policy_id": "HI-2024-88312", "line": "home"},
                             {"amount": "1247.00", "confirmation": "PAY-2026-55891", "next_renewal_date": "2027-10-28"})
    assert txt == ("Payment confirmed. Home insurance policy HI-2024-88312 — $1,247.00 paid. Confirmation: "
                   "PAY-2026-55891. Next renewal: Oct 2027. Questions? Call 1-800-CAA-HELP.")
