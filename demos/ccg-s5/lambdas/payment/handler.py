"""Connect-invoked payment Lambda for the secure segment (flow CCG-S5-SecurePayment).

Actions (LambdaInvocationAttributes.action):
  stash   field=pan|exp|cvv, value=$.StoredCustomerInput (ciphertext from Store customer input).
          Keeps the CIPHERTEXT for at most 10 minutes in the vault table, keyed by contact id. Nothing is decrypted.
  charge  Decrypts the three values in memory, validates, charges the simulated gateway once (idempotency key),
          deletes the vault row, writes the note to the synthetic system of record.
          Returns only: result, confirmation, brand, last4.

PCI rules applied here:
  - card values are never logged, never returned, never written to contact attributes;
  - the CVV is deleted with the vault row right after the authorization attempt (no storage after authorization);
  - the private key comes from Secrets Manager (PRIVATE_KEY_SECRET_ARN) and stays in memory.
Decryption uses the AWS Encryption SDK raw RSA keyring, which is how Connect encrypts Store customer input.

PARTNER_MODE=simulated_vendor (CCG demo): the same segment is presented as a hand-over to a third-party PCI
descoping partner (PCI Pal / Sycurio style). Nothing is sent to a vendor: start_session returns a fake vendor
session id and charge returns vendor-shaped fields (transaction id, masked PAN, auth code). Labelled "simulated".
"""
import base64
import hashlib
import os
import time

import boto3
import aws_encryption_sdk
from aws_encryption_sdk import CommitmentPolicy
from aws_encryption_sdk.identifiers import EncryptionKeyType, WrappingAlgorithm
from aws_encryption_sdk.internal.crypto.wrapping_keys import WrappingKey
from aws_encryption_sdk.key_providers.raw import RawMasterKeyProvider

import common as c

VAULT = c.ddb.Table(os.environ["VAULT_TABLE"])
KEY_ID = os.environ["ENCRYPTION_KEY_ID"]
PARTNER_MODE = os.environ.get("PARTNER_MODE", "native")  # native | simulated_vendor
_provider = None


class _ConnectKeyProvider(RawMasterKeyProvider):
    provider_id = "AmazonConnect"
    _pem = b""

    def _get_raw_key(self, key_id):
        return WrappingKey(wrapping_algorithm=WrappingAlgorithm.RSA_OAEP_SHA512_MGF1,
                           wrapping_key=self._pem, wrapping_key_type=EncryptionKeyType.PRIVATE)


def _make_provider(pem: bytes):
    provider = _ConnectKeyProvider()
    provider._pem = pem
    provider.add_master_key(KEY_ID)
    return provider


def _key_provider():
    global _provider
    if _provider is None:
        sm = boto3.client("secretsmanager")
        pem = sm.get_secret_value(SecretId=os.environ["PRIVATE_KEY_SECRET_ARN"])["SecretString"].encode()
        _provider = _make_provider(pem)
    return _provider


def _decrypt(ciphertext_b64: str) -> str:
    client = aws_encryption_sdk.EncryptionSDKClient(commitment_policy=CommitmentPolicy.REQUIRE_ENCRYPT_ALLOW_DECRYPT)
    plain, _ = client.decrypt(source=base64.b64decode(ciphertext_b64), key_provider=_key_provider())
    return plain.decode()


def _luhn(num: str) -> bool:
    digits = [int(d) for d in num][::-1]
    total = sum(digits[0::2]) + sum(sum(divmod(2 * d, 10)) for d in digits[1::2])
    return len(num) >= 13 and total % 10 == 0


def _brand(num: str) -> str:
    return {"4": "Visa", "5": "Mastercard", "3": "American Express"}.get(num[:1], "Card")


def _outcome_hint(num: str) -> str:
    """Simulated gateway rules for synthetic test cards (data/synthetic.json)."""
    return {"0002": "decline", "0119": "error"}.get(num[-4:], "approve")


def stash(contact_id, field, value):
    if field not in ("pan", "exp", "cvv"):
        return {"result": "bad_field"}
    if not value or value == "Timeout":
        return {"result": "input_timeout"}
    VAULT.update_item(Key={"contact_id": contact_id}, UpdateExpression=f"SET {field} = :v, expires_at = :e",
                      ExpressionAttributeValues={":v": value, ":e": int(time.time()) + 600})
    return {"result": "stashed"}


def _spell(confirmation):
    """'PAY-2026-55891' -> 'P A Y, 2 0 2 6, 5 5 8 9 1' so text-to-speech reads it character by character."""
    return ", ".join(" ".join(part) for part in str(confirmation).split("-"))


def charge(contact_id, policy_id, member_id, attempt="1", phone=""):
    p = c.policy(policy_id)
    if not p or p["member_id"] != member_id or p["plan_type"] != "full_pay":
        return {"result": "not_payable"}
    if p.get("status") == "paid":  # a retry can never charge a policy twice
        return {"result": "already_paid"}
    row = VAULT.get_item(Key={"contact_id": contact_id}, ConsistentRead=True).get("Item") or {}
    try:
        if not all(row.get(k) for k in ("pan", "exp", "cvv")):
            return {"result": "input_incomplete"}
        pan, exp, cvv = (_decrypt(row[k]) for k in ("pan", "exp", "cvv"))
        mm, yy = exp[:2], exp[2:4]
        if not _luhn(pan):
            return {"result": "invalid_card"}
        if not (len(exp) == 4 and "01" <= mm <= "12" and f"20{yy}-{mm}" >= c.now_iso()[:7]):
            return {"result": "card_expired"}
        if len(cvv) not in (3, 4):
            return {"result": "invalid_card"}
        last4, brand, hint = pan[-4:], _brand(pan), _outcome_hint(pan)
        del pan, cvv
        # one payment record per card attempt: a declined attempt must not block the caller's retry with another card
        key = c.idempotency_key(f"{contact_id}#{attempt}", policy_id, p["amount_due"])
        r = c.simulated_charge(key=key, policy_id=policy_id, member_id=member_id, amount=p["amount_due"],
                               method="new_card", last4=last4, brand=brand, outcome_hint=hint, channel="voice-secure-dtmf")
    finally:
        VAULT.delete_item(Key={"contact_id": contact_id})
    receipt = ""
    if r.get("result") == "approved":
        c.add_note(policy_id, member_id, f"Renewal paid by phone self-service, new {brand} ending {last4}, "
                   f"{p['amount_due']} CAD, confirmation {r['confirmation']}. Card entered by keypad, recording paused.",
                   contact_id)
        receipt = c.send_receipt_sms(phone, p, r) if hasattr(c, "send_receipt_sms") else ""
    out = {"result": r.get("result", "payment_error"), "confirmation": r.get("confirmation", ""),
           "brand": r.get("brand", brand), "last4": r.get("last4", last4)}
    if out["result"] == "approved":
        out.update({"amount_spoken": c.money_spoken(p["amount_due"]) if hasattr(c, "money_spoken") else str(p["amount_due"]),
                    "confirmation_spoken": _spell(out["confirmation"]), "receipt": receipt})
    if PARTNER_MODE == "simulated_vendor":
        out.update(_vendor_fields(contact_id, out))
    return out


def _vendor_fields(contact_id, out):
    """Shape of what a PCI descoping partner hands back to the flow: no card data, only references (simulated)."""
    digest = hashlib.sha256(contact_id.encode()).hexdigest().upper()
    status = {"approved": "AUTHORISED", "declined": "DECLINED"}.get(out["result"], "ERROR")
    return {"partner": "simulated", "partner_session_id": f"PS-{digest[:10]}", "partner_status": status,
            "partner_transaction_id": f"TX-{digest[10:22]}" if status == "AUTHORISED" else "",
            "partner_auth_code": digest[22:28] if status == "AUTHORISED" else "",
            "masked_pan": f"************{out['last4']}" if out.get("last4") else ""}


def handoff(contact_id):
    """After the ACXD escalation: why did the agent hand the call over? collect_new_card (+ policy/member) or agent."""
    if not c.T_HANDOFF:
        return {"reason": "agent"}
    item = c.T_HANDOFF.get_item(Key={"contact_id": contact_id}, ConsistentRead=True).get("Item") or {}
    source = "contact_id"
    if not item:  # safety net: the agent sent a different call id -> the one preparation made in the last 2 minutes
        now = int(time.time())
        recent = [i for i in c.T_HANDOFF.scan().get("Items", [])
                  if i.get("reason") == "collect_new_card" and int(i.get("expires_at", 0)) - 3600 >= now - 120]
        item, source = (recent[0], "recent_fallback") if len(recent) == 1 else ({}, f"none({len(recent)} recent)")
    print({"handoff": item.get("reason", "agent"), "matched_by": source})
    if item.get("reason") != "collect_new_card":
        return {"reason": "agent"}
    c.T_HANDOFF.delete_item(Key={"contact_id": item["contact_id"]})   # one keypad segment per preparation
    return {"reason": "collect_new_card", "policyId": item["policy_id"], "memberId": item["member_id"]}


def start_session(contact_id, policy_id):
    """First step of the hand-over: the partner session the flow 'transfers' into (simulated, no network call)."""
    p = c.policy(policy_id)
    if not p or p["status"] != "renewal_due":
        return {"result": "not_payable"}
    digest = hashlib.sha256(contact_id.encode()).hexdigest().upper()
    return {"result": "session_started", "partner_mode": PARTNER_MODE,
            "partner_session_id": f"PS-{digest[:10]}" if PARTNER_MODE == "simulated_vendor" else "",
            "amount": str(p["amount_due"])}


def handler(event, _ctx):
    data = event["Details"]
    params = data.get("Parameters", {})
    contact_id = data["ContactData"]["InitialContactId"]
    try:
        if params.get("action") == "start_session":
            out = start_session(contact_id, params.get("policyId", ""))
        elif params.get("action") == "stash":
            out = stash(contact_id, params.get("field"), params.get("value"))
        elif params.get("action") == "handoff":
            out = handoff(contact_id)
        elif params.get("action") == "charge":
            phone = (data["ContactData"].get("CustomerEndpoint") or {}).get("Address", "")
            out = charge(contact_id, params.get("policyId", ""), params.get("memberId", ""), params.get("attempt") or "1",
                         phone)
        else:
            out = {"result": "bad_action"}
    except Exception as e:  # noqa: BLE001 - never surface details (could contain input) to the flow
        print({"action": params.get("action"), "error_type": type(e).__name__})
        out = {"result": "payment_error"}
    return {k: str(v) for k, v in out.items()}
