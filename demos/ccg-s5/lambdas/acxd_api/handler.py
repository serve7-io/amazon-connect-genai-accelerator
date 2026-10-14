"""HTTP API for the ACXD application's integrations (Lambda function URL).

POST /<operation> with a JSON body; header x-api-key must match API_KEY (set by CloudFormation from the
Secrets Manager secret with a dynamic reference, so the key never appears in the template or in chat).
Operations: lookup_member, verify_member, get_policies, get_payment_methods, charge_stored_card, payment_status, prepare_card_entry,
add_note.
Never receives or returns card numbers: only tokens, brand and last 4 digits.
"""
import hmac
import json
import os

import common as c


def _resp(code, body):
    return {"statusCode": code, "headers": {"content-type": "application/json"}, "body": json.dumps(body, default=str)}


def _digits(s):
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _membership(raw):
    """'M-60425183', 'm 60425183', '60425183' -> 'M-60425183'."""
    d = _digits(raw)
    return f"M-{d}" if d else ""


def _dob(raw):
    """Accepts 1975-06-12, 1975/06/12, June 12 1975, 12 June 1975, 06/12/1975 (month first) -> 1975-06-12."""
    import datetime as dt, re
    txt = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(raw or "").lower().replace(",", " ").replace(" of ", " "))
    txt = " ".join(txt.split())
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y", "%m/%d/%Y", "%Y %m %d"):
        try:
            return dt.datetime.strptime(txt, fmt).date().isoformat()
        except ValueError:
            continue
    return txt


_SPOKEN = {"ZERO": "0", "OH": "0", "ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5", "SIX": "6",
           "SEVEN": "7", "EIGHT": "8", "NINE": "9", "BEE": "B", "SEE": "C", "DEE": "D", "KAY": "K", "AY": "A", "EM": "M"}


def _postal(raw):
    """'K1A 0B1', 'k1a0b1', 'K one A zero B one' -> 'K1A0B1'."""
    words = str(raw or "").upper().replace("-", " ").split()
    return "".join(_SPOKEN.get(w, w) for w in words if w).replace(" ", "") if any(w in _SPOKEN for w in words) \
        else "".join(ch for ch in str(raw or "").upper() if ch.isalnum())


def _vehicle_matches(member_vehicle, said):
    """Service level: the caller confirms the vehicle on file ('yes', 'that's the one', or make/model words)."""
    said = str(said or "").lower()
    if said in ("yes", "y", "true", "confirmed"):
        return True
    words = [w for w in str(member_vehicle or "").lower().split() if not w.isdigit()]
    return bool(words) and any(w in said for w in words)


# Shared authentication procedure (all scenarios): the security level comes from the interaction type.
AUTH_LEVELS = {
    "payment": ("dob", "postal_code"),            # payment transactions: date of birth + postal code
    "account_change": ("postal_code", "phone"),   # account changes: postal code + phone on file
    "service": ("vehicle",),                      # service requests: membership number + vehicle confirmation
}


def _member_by_phone(raw):
    """Phone number on the account (any format, last 10 digits) -> member, or None. Small demo table: scan."""
    d = _digits(raw)[-10:]
    if len(d) < 10:
        return None
    items, kw = [], {}
    while True:
        page = c.T_MEMBERS.scan(**kw)
        items += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            break
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return next((m for m in items if _digits(m.get("phone"))[-10:] == d), None)


def lookup_member(b):
    """CRM lookup (shared by all scenarios): membership number OR the phone number on the account.
    Returns who the member is (not yet verified): first name, tier, vehicle, member since, service history, open cases."""
    raw = b.get("membership_number") or b.get("member_id") or ""
    m = c.member(_membership(raw)) if raw else None
    if not m:
        m = _member_by_phone(b.get("phone") or raw)
    if not m:
        return {"found": False}
    return {"found": True, "member_id": m["member_id"], "first_name": m["name"].split()[0], "tier": m.get("tier", ""),
            "vehicle": m.get("vehicle", ""), "vehicle_plate": m.get("vehicle_plate", ""),
            "member_since": m.get("member_since", ""), "service_history": m.get("service_history", []),
            "open_cases": int(m.get("open_cases", 0))}


def verify_member(b):
    """Verify the caller for an interaction type (payment | account_change | service). Never says which factor failed.
    Legacy mode (no membership number): policy number + date of birth."""
    if not (b.get("membership_number") or b.get("member_id")):
        policy_no = (b.get("policy_number") or "").upper().replace(" ", "").replace("-", "")
        p = next((x for x in c.T_POLICIES.scan()["Items"] if x["policy_id"].replace("-", "") == policy_no), None) if policy_no else None
        m = c.member(p["member_id"]) if p else None
        ok = bool(m) and m["dob"] == _dob(b.get("dob"))
        level = "payment"
    else:
        m = c.member(_membership(b.get("membership_number") or b.get("member_id")))
        level = b.get("interaction_type") or "payment"
        missing = [f for f in AUTH_LEVELS.get(level, ()) if not str(b.get(f) or "").strip()]
        if missing:  # asked too early: not a failed attempt, just tell the agent what to collect first
            return {"verified": False, "missing": missing}
        checks = {"dob": lambda: m["dob"] == _dob(b.get("dob")),
                  "postal_code": lambda: _postal(m.get("postal_code")) == _postal(b.get("postal_code")),
                  "phone": lambda: _digits(m.get("phone"))[-10:] == _digits(b.get("phone"))[-10:] != "",
                  "vehicle": lambda: _vehicle_matches(m.get("vehicle"), b.get("vehicle"))}
        ok = bool(m) and level in AUTH_LEVELS and all(checks[f]() for f in AUTH_LEVELS[level])
    if not ok:
        return {"verified": False}
    return {"verified": True, "member_id": m["member_id"], "first_name": m["name"].split()[0], "tier": m.get("tier", ""),
            "level": level, "sms_capable": bool(m.get("sms_capable")),
            "caller_id_matches": _digits(m["phone"])[-10:] == _digits(b.get("caller_phone") or b.get("phone"))[-10:]}


def get_policies(b):
    out = []
    for p in c.policies_for(b["member_id"]):
        out.append({"policy_id": p["policy_id"], "policy_last4": p["policy_id"][-4:], "line": p["line"],
                    "line_name": c.LINE_NAMES.get(p["line"], p["line"]),
                    "renewal_date": p["renewal_date"], "plan_type": p["plan_type"], "amount_due": str(p["amount_due"]),
                    "amount_spoken": c.money_spoken(p["amount_due"]), "currency": p["currency"], "status": p["status"],
                    "payable_here": p["status"] == "renewal_due" and p["plan_type"] == "full_pay"})
    return {"policies": out}


def _methods(member_id):
    return c.T_METHODS.query(KeyConditionExpression=c.Key("member_id").eq(member_id))["Items"]


def get_payment_methods(b):
    month = c.now_iso()[:7]
    return {"methods": [{"token": i["token"], "brand": i["brand"], "last4": i["last4"], "expired": i["expiry"] < month}
                        for i in _methods(b["member_id"])]}


def charge_stored_card(b):
    p = c.policy(b["policy_id"])
    if not p or p["member_id"] != b["member_id"] or p["plan_type"] != "full_pay":
        return {"result": "not_payable"}
    if p["status"] == "paid":
        return {"result": "already_paid"}
    m = next((i for i in _methods(b["member_id"]) if i["token"] == b.get("token")), None)
    if not m:
        return {"result": "unknown_card"}
    if m["expiry"] < c.now_iso()[:7]:
        return {"result": "card_expired"}
    key = c.idempotency_key(b["contact_id"], p["policy_id"], p["amount_due"])
    r = c.simulated_charge(key=key, policy_id=p["policy_id"], member_id=p["member_id"], amount=p["amount_due"],
                           method="stored", last4=m["last4"], brand=m["brand"], outcome_hint="approve",
                           channel="voice-acxd")
    out = {k: r.get(k) for k in ("result", "confirmation", "last4", "brand", "amount")}
    if r.get("result") == "approved":
        c.add_note(p["policy_id"], p["member_id"], f"Renewal paid by phone self-service, {m['brand']} ending "
                   f"{m['last4']}, {p['amount_due']} CAD, confirmation {r['confirmation']}.", b["contact_id"])
        out["receipt"] = c.send_receipt_sms(b.get("phone", ""), p, r)
        out["next_renewal"] = c.month_year(r.get("next_renewal_date", ""))
    return out


def payment_status(b):
    """Outcome of this call's payment for the policy: stored card, or the keypad attempts in the secure segment.
    An approved attempt wins; otherwise the latest attempt. 'none' = nothing was charged."""
    p = c.policy(b["policy_id"])
    if not p:
        return {"result": "none", "confirmation": "", "attempts": 0}
    keys = [c.idempotency_key(b["contact_id"], p["policy_id"], p["amount_due"])]
    keys += c.keypad_attempt_keys(b["contact_id"], p["policy_id"], p["amount_due"])
    rows = [r for r in (c.existing_payment(k) for k in keys) if r]
    if not rows and hasattr(c, "T_PAYMENTS"):  # safety net if the call id did not match: this policy, last 10 minutes
        import datetime as dt
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)).isoformat(timespec="seconds")
        rows = sorted([r for r in c.T_PAYMENTS.scan().get("Items", [])
                       if r.get("policy_id") == p["policy_id"] and r.get("created_at", "") >= cutoff],
                      key=lambda r: r.get("created_at", ""))
        print({"payment_status": "recent_fallback", "rows": len(rows)})
    best = next((r for r in rows if r.get("result") == "approved"), rows[-1] if rows else {})
    approved = best.get("result") == "approved"
    return {"result": best.get("result", "none"), "confirmation": best.get("confirmation", ""),
            "brand": best.get("brand", ""), "last4": best.get("last4", ""), "attempts": len(rows),
            "policy_id": p["policy_id"], "line_name": c.LINE_NAMES.get(p.get("line", ""), ""),
            "amount_spoken": c.money_spoken(p["amount_due"]),
            "next_renewal": c.month_year(best.get("next_renewal_date") or c.next_renewal(p.get("renewal_date", ""))) if approved else "",
            "receipt": "sms_sent" if best.get("receipt") == "sms_sent" else "none"}


def prepare_card_entry(b):
    """Agent -> phone system: the caller agreed to pay this policy with a new card on the keypad.
    Stored per contact for 1 hour; the Connect flow reads it after the ACXD escalation. No card data here."""
    p = c.policy(b["policy_id"])
    if not p or p["member_id"] != b["member_id"] or p["plan_type"] != "full_pay":
        print({"prepare_card_entry": "not_payable", "policy_found": bool(p)})
        return {"ready": False, "reason": "not_payable"}
    if p["status"] == "paid":
        return {"ready": False, "reason": "already_paid"}
    print({"prepare_card_entry": "ready", "contact_id_is_uuid": bool(_UUID.fullmatch(str(b["contact_id"])))})
    c.T_HANDOFF.put_item(Item={"contact_id": b["contact_id"], "reason": "collect_new_card", "policy_id": p["policy_id"],
                               "member_id": p["member_id"], "created_at": c.now_iso(),
                               "expires_at": int(c.time.time()) + 3600})
    return {"ready": True, "amount": str(p["amount_due"]), "currency": p["currency"]}


def add_note(b):
    c.add_note(b["policy_id"], b["member_id"], b["note"], b.get("contact_id", ""))
    return {"saved": True}


def _fields(body):
    """ACXD may send our fields at the top level or wrapped (sendContext: payload/data/request + context).
    Flatten to one dict; log only the key names (no values) so the shape can be checked in CloudWatch."""
    flat = {}
    if isinstance(body, dict):
        for wrapper in ("context", "data", "request", "payload"):     # later wins: payload over context
            if isinstance(body.get(wrapper), dict):
                flat.update(body[wrapper])
        flat.update({k: v for k, v in body.items() if not isinstance(v, dict)})
        # The call id must come from the phone system, not from the model: take contact_id from ACXD's context.
        ctx_cid = _find_contact_id(body.get("nlx_context"))
        said = str(flat.get("contact_id") or "")
        if ctx_cid:
            flat["contact_id"] = ctx_cid
        print({"body_keys": sorted(body), "used_keys": sorted(flat),
               "ctx_keys": sorted(body["nlx_context"]) if isinstance(body.get("nlx_context"), dict) else None,
               "contact_id_source": "context" if ctx_cid else ("model" if said else "none"),
               "model_contact_id_ok": (said == ctx_cid) if (ctx_cid and said) else None,
               "model_contact_id_is_uuid": bool(_UUID.fullmatch(said)) if said else None})
    return flat


import re as _re
_UUID = _re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", _re.I)


def _find_contact_id(ctx, depth=0):
    """Search ACXD's nlx_context for our context variable contact_id (any nesting) and return it if it is a UUID."""
    if depth > 4 or ctx is None:
        return ""
    if isinstance(ctx, dict):
        for k in ("contact_id", "contactId", "InitialContactId", "initialContactId"):
            v = ctx.get(k)
            if isinstance(v, str) and _UUID.fullmatch(v.strip()):
                return v.strip()
        for v in ctx.values():
            found = _find_contact_id(v, depth + 1)
            if found:
                return found
    elif isinstance(ctx, list):
        for v in ctx:
            found = _find_contact_id(v, depth + 1)
            if found:
                return found
    return ""


OPS = {f.__name__: f for f in (lookup_member, verify_member, get_policies, get_payment_methods, charge_stored_card,
                               payment_status, prepare_card_entry, add_note)}


def handler(event, _ctx):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not hmac.compare_digest(headers.get("x-api-key", ""), os.environ["API_KEY"]):
        return _resp(401, {"error": "unauthorized"})
    op = (event.get("rawPath") or "/").strip("/")
    if op not in OPS:
        return _resp(404, {"error": "unknown operation", "operations": sorted(OPS)})
    try:
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64
            raw = base64.b64decode(raw).decode()
        return _resp(200, OPS[op](_fields(json.loads(raw))))
    except KeyError as e:
        return _resp(400, {"error": f"missing field {e}"})
