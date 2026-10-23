"""Shared helpers: synthetic billing/policy store (DynamoDB) and the simulated payment gateway."""
import datetime as dt
import hashlib
import os
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

ddb = boto3.resource("dynamodb")
ssm = boto3.client("ssm")
T_MEMBERS = ddb.Table(os.environ["MEMBERS_TABLE"])
T_POLICIES = ddb.Table(os.environ["POLICIES_TABLE"])
T_METHODS = ddb.Table(os.environ["METHODS_TABLE"])
T_PAYMENTS = ddb.Table(os.environ["PAYMENTS_TABLE"])
T_NOTES = ddb.Table(os.environ["NOTES_TABLE"])
T_HANDOFF = ddb.Table(os.environ["HANDOFF_TABLE"]) if os.environ.get("HANDOFF_TABLE") else None


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def gateway_mode() -> str:
    """normal | down | slow. Flipped live in the demo for the 'payment system is down' objection."""
    try:
        return ssm.get_parameter(Name=os.environ["GATEWAY_MODE_PARAM"])["Parameter"]["Value"]
    except Exception:  # noqa: BLE001 - mode lookup must never break a payment path
        return "normal"


def member(member_id):
    return T_MEMBERS.get_item(Key={"member_id": member_id}).get("Item")


def policies_for(member_id):
    return T_POLICIES.query(IndexName="by_member", KeyConditionExpression=Key("member_id").eq(member_id))["Items"]


def policy(policy_id):
    return T_POLICIES.get_item(Key={"policy_id": policy_id}).get("Item")


def keypad_attempt_keys(contact_id, policy_id, amount):
    """Idempotency keys of the secure keypad attempts (the payment Lambda uses contact#attempt, max 3)."""
    return [idempotency_key(f"{contact_id}#{n}", policy_id, amount) for n in (1, 2, 3)]


def idempotency_key(contact_id, policy_id, amount):
    return hashlib.sha256(f"{contact_id}|{policy_id}|{amount}".encode()).hexdigest()[:32]


def existing_payment(key):
    return T_PAYMENTS.get_item(Key={"idempotency_key": key}).get("Item")


def simulated_charge(*, key, policy_id, member_id, amount, method, last4, brand, outcome_hint, channel):
    """Records exactly one payment per idempotency key. outcome_hint: approve | decline | error."""
    prior = existing_payment(key)
    if prior:
        return prior
    mode = gateway_mode()
    if mode == "down" or outcome_hint == "error":
        return {"result": "gateway_unavailable"}
    if mode == "slow":
        time.sleep(4)
    approved = outcome_hint == "approve"
    item = {
        "idempotency_key": key, "policy_id": policy_id, "member_id": member_id, "amount": Decimal(str(amount)),
        "currency": "CAD", "method": method, "brand": brand, "last4": last4, "channel": channel,
        "result": "approved" if approved else "declined", "created_at": now_iso(),
        "confirmation": f"PAY-{now_iso()[:4]}-{int(time.time()) % 100000:05d}" if approved else "",
    }
    try:
        T_PAYMENTS.put_item(Item=item, ConditionExpression="attribute_not_exists(idempotency_key)")
    except ddb.meta.client.exceptions.ConditionalCheckFailedException:
        return existing_payment(key)
    if approved:  # billing write-back: renewal paid, next renewal one year after this one
        p = policy(policy_id) or {}
        nxt = next_renewal(p.get("renewal_date", ""))
        T_POLICIES.update_item(Key={"policy_id": policy_id},
                               UpdateExpression="SET #s = :p, paid_at = :t, last_confirmation = :c, next_renewal_date = :n",
                               ExpressionAttributeNames={"#s": "status"},
                               ExpressionAttributeValues={":p": "paid", ":t": item["created_at"],
                                                          ":c": item["confirmation"], ":n": nxt})
        item["next_renewal_date"] = nxt
    return item


def next_renewal(renewal_date):
    """'2026-10-27' -> '2027-10-27' (empty if unknown)."""
    try:
        return f"{int(renewal_date[:4]) + 1}{renewal_date[4:]}"
    except (ValueError, TypeError):
        return ""


LINE_NAMES = {"home": "home insurance", "property": "home insurance", "auto": "auto insurance"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def month_year(iso_date):
    """'2027-10-27' -> 'October 2027'."""
    try:
        return f"{MONTHS[int(iso_date[5:7]) - 1]} {iso_date[:4]}"
    except (ValueError, IndexError, TypeError):
        return ""


def money_spoken(amount):
    """1247.00 -> '$1,247'; 1284.6 -> '$1,284.60'."""
    d = Decimal(str(amount))
    return f"${d:,.0f}" if d == d.to_integral_value() else f"${d:,.2f}"


def receipt_text(p, item):
    """SMS receipt, CAA demo script wording. No card data."""
    line = LINE_NAMES.get(p.get("line", ""), "insurance").capitalize()
    nxt = month_year(item.get("next_renewal_date", ""))
    nxt = f" Next renewal: {nxt[:3]} {nxt[-4:]}." if nxt else ""
    return (f"Payment confirmed. {line} policy {p['policy_id']} — ${Decimal(str(item['amount'])):,.2f} paid. "
            f"Confirmation: {item['confirmation']}.{nxt} Questions? Call 1-800-CAA-HELP.")


def send_receipt_sms(phone, p, item):
    """Outbound SMS receipt after an approved payment (Amazon SNS). Never fails the payment: returns
    sent | skipped | failed. SMS_ENABLED=false or no E.164 number -> skipped. The number is not logged."""
    if os.environ.get("SMS_ENABLED", "false").lower() != "true" or not str(phone or "").startswith("+"):
        return "skipped"
    try:
        boto3.client("sns").publish(PhoneNumber=phone, Message=receipt_text(p, item),
                                    MessageAttributes={"AWS.SNS.SMS.SMSType": {"DataType": "String",
                                                                               "StringValue": "Transactional"}})
        if item.get("idempotency_key"):
            T_PAYMENTS.update_item(Key={"idempotency_key": item["idempotency_key"]}, UpdateExpression="SET receipt = :r",
                                   ExpressionAttributeValues={":r": "sms_sent"})
        return "sent"
    except Exception as e:  # noqa: BLE001 - a receipt problem must never undo or block a payment
        print({"sms": "failed", "error_type": type(e).__name__})
        return "failed"


def add_note(policy_id, member_id, text, contact_id):
    T_NOTES.put_item(Item={"policy_id": policy_id, "created_at": now_iso(), "member_id": member_id,
                           "contact_id": contact_id, "note": text[:1000]})
