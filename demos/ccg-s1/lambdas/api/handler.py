"""CAA Scenario 1 (persistent cross-channel roadside assistance): Lambda ccg-s1-api. Synthetic data only.

Two event shapes, one set of operations (built to Vanja's Scenario 1 build brief, 15 Oct 2026):
1. Function URL, from ACXD data requests: POST /<op>, header x-api-key, JSON body. Returns JSON.
2. Amazon Connect Invoke Lambda, from flows and guides: op in Details.Parameters.op, the other parameters next to it.
   Returns a flat string map (Connect reads it as $.External.<key>).
Also: GET / is the member portal demo page with the Connect chat widget, and POST /health is used by the smoke test.

Operations (every response has a ready-to-say "message"; errors add "error" and, where useful, "valid_options"):
  lookupmember   member_id or phone -> member card (first name, tier, vehicle, member since, prior calls, last service)
  verifyservice  shared rule table by interaction_type: service = membership number + vehicle,
                 account_change = postal code + phone, payment = date of birth + postal code. Never names the failing factor.
  coverage       tier + service -> covered, tow km included, cost to member
  dispatch       creates a dispatch record (provider, driver, ETA, destination). Shared with Scenario 3.
  writeback      mock ServiceNow: writes the service record for the contact
  reset          restores the member's demo state (seed rows back, their dispatches and service records removed)
"""
import datetime as dt
import hmac
import json
import os
import random
import re
from decimal import Decimal
from pathlib import Path

SEED = json.loads((Path(__file__).resolve().parent / "seed.json").read_text())

# Output name in seed.json -> environment variable holding the table name
TABLE_ENV = {"MembersTable": "MEMBERS_TABLE", "ServiceHistoryTable": "HISTORY_TABLE", "ProvidersTable": "PROVIDERS_TABLE",
             "DispatchesTable": "DISPATCHES_TABLE", "ServiceRecordsTable": "RECORDS_TABLE"}
KEYS = {"MembersTable": ["member_id"], "ServiceHistoryTable": ["member_id", "event_date"],
        "ProvidersTable": ["provider_id"], "DispatchesTable": ["dispatch_id"], "ServiceRecordsTable": ["contact_id"]}

# Coverage by tier. Plus 200 km comes from the demo script; the other values are demo values (not CCG facts).
COVERAGE = {
    "Basic": {"tow_km": 10, "demo_value": True},
    "Plus": {"tow_km": 200, "demo_value": False},
    "Premier": {"tow_km": 320, "demo_value": True},
}
SERVICES = {"tow": "towing", "tire": "a flat tire change", "boost": "a battery boost", "lockout": "a lockout",
            "fuel": "fuel delivery"}
AUTH_LEVELS = {  # shared rule table (Scenarios 1, 2 and 5 can reuse it); Scenario 5 is NOT repointed here
    "service": ("vehicle_confirmed",),
    "account_change": ("postal_code", "phone"),
    "payment": ("dob", "postal_code"),
}
AUTH_METHOD = {"service": "membership number + vehicle", "account_change": "postal code + phone",
               "payment": "date of birth + postal code"}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


# ---------- data access (boto3; replaced by an in-memory fake in tests) ----------
class Db:
    def __init__(self):
        import boto3
        from boto3.dynamodb.conditions import Key
        self.Key, ddb = Key, boto3.resource("dynamodb")
        self.t = {name: ddb.Table(os.environ[env]) for name, env in TABLE_ENV.items()}

    def get(self, table, key):
        return self.t[table].get_item(Key=key).get("Item")

    def put(self, table, item):
        self.t[table].put_item(Item=item)

    def delete(self, table, key):
        self.t[table].delete_item(Key=key)

    def query(self, table, attr, value, index=None):
        kw = {"KeyConditionExpression": self.Key(attr).eq(value)}
        if index:
            kw["IndexName"] = index
        return self.t[table].query(**kw).get("Items", [])

    def scan(self, table):
        items, kw = [], {}
        while True:
            p = self.t[table].scan(**kw)
            items += p.get("Items", [])
            if "LastEvaluatedKey" not in p:
                return items
            kw["ExclusiveStartKey"] = p["LastEvaluatedKey"]


DB = None


def db():
    global DB
    if DB is None:
        DB = Db()
    return DB


# ---------- helpers ----------
def _digits(s):
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _member_id(raw):
    d = _digits(raw)
    return f"M-{d}" if d else ""


def _phone_key(raw):
    return _digits(raw)[-10:]


def _num(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return _num(o)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _vehicle_spoken(m):
    v = m.get("vehicle") or {}
    return " ".join(str(_num(v[k])) for k in ("year", "make", "model") if v.get(k))


def _month_year(yyyy_mm):
    try:
        return dt.date.fromisoformat(f"{yyyy_mm}-01").strftime("%B %Y")
    except ValueError:
        return yyyy_mm


def _member(b):
    """Find the member by member_id, or by the phone number on the account."""
    mid = _member_id(b.get("member_id") or b.get("membership_number"))
    if mid:
        return db().get("MembersTable", {"member_id": mid})
    pk = _phone_key(b.get("phone"))
    if pk:
        rows = db().query("MembersTable", "phone_key", pk, index="phone-index")
        return rows[0] if rows else None
    return None


def _not_found(b):
    if not (b.get("member_id") or b.get("membership_number") or b.get("phone")):
        return {"found": False, "error": "missing_fields", "valid_options": ["member_id", "phone"],
                "message": "Could you give me your membership number, or the phone number on your account?"}
    return {"found": False, "error": "member_not_found", "valid_options": ["member_id", "phone"],
            "message": "I couldn't find that membership number. Could you check it, or give me the phone number on the account?"}


def _vehicle_matches(member, said):
    said = str(said or "").lower()
    if said in ("yes", "y", "true", "confirmed", "correct"):
        return True
    v = member.get("vehicle") or {}
    words = [str(v.get(k, "")).lower() for k in ("make", "model") if v.get(k)]
    return bool(words) and any(w in said or w.replace("-", "") in said.replace("-", "") for w in words)


def _postal(raw):
    return "".join(ch for ch in str(raw or "").upper() if ch.isalnum())


def _dob(raw):
    txt = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(raw or "").lower().replace(",", " ").replace(" of ", " "))
    txt = " ".join(txt.split())
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(txt, fmt).date().isoformat()
        except ValueError:
            continue
    return txt


# ---------- operations ----------
def lookupmember(b):
    m = _member(b)
    if not m:
        return _not_found(b)
    hist = sorted(db().query("ServiceHistoryTable", "member_id", m["member_id"]), key=lambda r: r["event_date"])
    last = hist[-1] if hist else None
    last_service = f"{last['service']}, {_month_year(last['event_date'])}" if last else ""
    first = m["first_name"]
    prior = len(hist)
    calls = f"{prior} prior roadside call" + ("" if prior == 1 else "s")
    return {"found": True, "member_id": m["member_id"], "first_name": first, "tier": m["tier"],
            "vehicle_spoken": _vehicle_spoken(m), "member_since": str(m.get("member_since", "")),
            "prior_calls": prior, "last_service": last_service, "open_cases": int(_num(m.get("open_cases", 0))),
            "message": f"Thanks, {first}. I found your {m['tier']} membership. "
                       f"Is your vehicle the {_vehicle_spoken(m)}?" if m.get("vehicle") else
                       f"Thanks, {first}. I found your {m['tier']} membership.",
            "agent_note": f"Member since {m.get('member_since', '')}. {calls}"
                          + (f" (last: {last_service})." if last_service else ".")}


def verifyservice(b):
    level = str(b.get("interaction_type") or "service").strip().lower()
    if level not in AUTH_LEVELS:
        return {"verified": False, "error": "unknown_interaction_type", "valid_options": sorted(AUTH_LEVELS),
                "message": "I can't verify that type of request here."}
    m = _member(b)
    if not m:
        return {"verified": False, **_not_found(b)}
    if level == "service" and not b.get("vehicle_confirmed") and b.get("vehicle"):
        b = {**b, "vehicle_confirmed": b["vehicle"]}
    missing = [f for f in AUTH_LEVELS[level] if not str(b.get(f) or "").strip()]
    if missing:  # asked too early: not a failed attempt
        return {"verified": False, "error": "missing_fields", "valid_options": missing,
                "message": "Before I check, I need a bit more information."}
    checks = {"vehicle_confirmed": lambda: _vehicle_matches(m, b.get("vehicle_confirmed")),
              "postal_code": lambda: _postal(m.get("postal_code")) == _postal(b.get("postal_code")),
              "phone": lambda: _phone_key(m.get("phone")) == _phone_key(b.get("phone")) != "",
              "dob": lambda: m.get("dob") == _dob(b.get("dob"))}
    if not all(checks[f]() for f in AUTH_LEVELS[level]):
        return {"verified": False, "error": "not_verified", "auth_level": level, "auth_status": "Not verified",
                "message": "That doesn't match what we have on file. Could you check the details and try again?"}
    return {"verified": True, "auth_level": level, "auth_status": "Verified", "method": AUTH_METHOD[level],
            "member_id": m["member_id"], "message": f"Thank you, {m['first_name']}. You're verified."}


def coverage(b):
    m = _member(b)
    if not m:
        return {"covered": False, **_not_found(b)}
    service = str(b.get("service") or "tow").strip().lower()
    if service not in SERVICES:
        return {"covered": False, "error": "unknown_service", "valid_options": sorted(SERVICES),
                "message": "Which service do you need: a tow, a tire change, a battery boost, a lockout or fuel delivery?"}
    tier = COVERAGE.get(m["tier"], COVERAGE["Basic"])
    location = str(b.get("location") or "").strip()
    safety = str(b.get("safety") or "").strip()
    spare = str(b.get("spare") or "").strip()
    out = {"covered": True, "tier": m["tier"], "service": service, "tow_km_included": tier["tow_km"],
           "cost_to_member": 0, "demo_value": tier["demo_value"], "location": location, "safety": safety, "spare": spare}
    if service == "tow":
        out["message"] = f"Your {m['tier']} membership covers up to {tier['tow_km']} km of towing at no charge."
    else:
        out["message"] = f"Your {m['tier']} membership covers {SERVICES[service]} at no charge."
    # Hand-off summary for the live agent, built only from checked data and what the member said (no model text).
    parts = [f"{m['first_name']} {m.get('last_name', '')}".strip() + f", {m['tier']} member {m['member_id']}."]
    if m.get("vehicle"):
        parts.append(f"Vehicle: {_vehicle_spoken(m)}.")
    if location:
        parts.append(f"Location: {location}.")
    if safety:
        parts.append(f"Safety: {safety}.")
    if spare:
        parts.append(f"Spare tire: {spare}.")
    parts.append(f"Needs {SERVICES[service]}" + (f", {tier['tow_km']} km covered at no charge." if service == "tow" else
                                                  ", covered at no charge."))
    out["summary"] = " ".join(parts)
    return out


def _provider(region):
    rows = [p for p in db().scan("ProvidersTable") if p.get("certified") and p.get("region") == region]
    rows = rows or [p for p in db().scan("ProvidersTable") if p.get("certified")]
    return sorted(rows, key=lambda p: _num(p.get("rank", 99)))[0] if rows else None


def dispatch(b):
    m = _member(b)
    if not m:
        return {"dispatched": False, **_not_found(b)}
    location = str(b.get("location") or "").strip()
    if not location:
        return {"dispatched": False, "error": "missing_fields", "valid_options": ["location"],
                "message": "I need the vehicle's location before I can send a truck."}
    service = str(b.get("service") or "tow").strip().lower()
    if service not in SERVICES:
        return {"dispatched": False, "error": "unknown_service", "valid_options": sorted(SERVICES),
                "message": "Which service should the driver bring?"}
    p = _provider(str(b.get("region") or "Kingston"))
    if not p:
        return {"dispatched": False, "error": "no_provider", "message": "No provider is available right now."}
    now = _now()
    dispatch_id = f"D-{now:%Y%m%d}-{random.randint(1000, 9999)}"
    destination = p["destination"] if str(b.get("destination") or "nearest_certified") == "nearest_certified" \
        else str(b["destination"])
    item = {"dispatch_id": dispatch_id, "member_id": m["member_id"], "contact_id": str(b.get("contact_id") or ""),
            "service": service, "location": location, "provider_id": p["provider_id"], "provider": p["name"],
            "driver": p["driver"], "eta_min_low": int(_num(p["eta_min_low"])), "eta_min_high": int(_num(p["eta_min_high"])),
            "destination": destination, "shop_hours_today": p.get("shop_hours_today", ""), "status": "dispatched",
            "created_at": now.isoformat(timespec="seconds")}
    db().put("DispatchesTable", item)
    return {"dispatched": True, **{k: item[k] for k in ("dispatch_id", "provider", "driver", "eta_min_low", "eta_min_high",
                                                          "destination", "shop_hours_today")},
            "message": f"{p['name']} is on the way. Your driver is {p['driver']}, arriving in about "
                       f"{item['eta_min_low']} to {item['eta_min_high']} minutes. They'll take your vehicle to the "
                       f"{destination}."}


def writeback(b):
    cid = str(b.get("contact_id") or "").strip()
    if not cid:
        return {"written": False, "error": "missing_fields", "valid_options": ["contact_id"],
                "message": "The service record needs the contact id."}
    now = _now()
    item = {"contact_id": cid, "record_id": f"SN-INC{random.randint(1000000, 9999999)}",
            "member_id": _member_id(b.get("member_id")), "disposition": str(b.get("disposition") or ""),
            "dispatch_id": str(b.get("dispatch_id") or ""), "summary": str(b.get("summary") or "")[:2000],
            "written_at": now.isoformat(timespec="seconds"), "system": "ServiceNow (mock)"}
    db().put("ServiceRecordsTable", item)
    return {"written": True, "record_id": item["record_id"], "written_at": item["written_at"],
            "message": f"Service record {item['record_id']} saved."}


def reset(b):
    mid = _member_id(b.get("member_id")) or "M-20481537"
    tables = SEED["tables"]
    member = next((r for r in tables["MembersTable"] if r["member_id"] == mid), None)
    if not member:
        return {"ok": False, "error": "member_not_found", "message": "That member is not in the demo seed."}
    d = db()
    d.put("MembersTable", _decimals(member))
    for r in d.query("ServiceHistoryTable", "member_id", mid):
        d.delete("ServiceHistoryTable", {"member_id": mid, "event_date": r["event_date"]})
    for r in tables["ServiceHistoryTable"]:
        if r["member_id"] == mid:
            d.put("ServiceHistoryTable", _decimals(r))
    removed = 0
    for r in d.scan("DispatchesTable"):
        if r.get("member_id") == mid:
            d.delete("DispatchesTable", {"dispatch_id": r["dispatch_id"]})
            removed += 1
    for r in d.scan("ServiceRecordsTable"):
        if r.get("member_id") == mid:
            d.delete("ServiceRecordsTable", {"contact_id": r["contact_id"]})
            removed += 1
    return {"ok": True, "member_id": mid, "removed": removed, "message": f"Demo state for {mid} restored."}


def _decimals(o):
    if isinstance(o, float):
        return Decimal(str(o))
    if isinstance(o, dict):
        return {k: _decimals(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_decimals(v) for v in o]
    return o


OPS = {f.__name__: f for f in (lookupmember, verifyservice, coverage, dispatch, writeback, reset)}


# ---------- Amazon Connect (Invoke Lambda) ----------
def _flat(d, prefix=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}_"))
        elif isinstance(v, list):
            out[f"{prefix}{k}"] = ", ".join(map(str, v))
        elif isinstance(v, bool):
            out[f"{prefix}{k}"] = "true" if v else "false"
        else:
            out[f"{prefix}{k}"] = "" if v is None else str(_num(v))
    return out


def connect_invoke(event):
    det = event.get("Details") or {}
    params = dict(det.get("Parameters") or {})
    op = str(params.pop("op", "")).strip()
    if op not in OPS:
        return {"error": "unknown_operation", "valid_options": ", ".join(sorted(OPS)), "message": ""}
    contact = det.get("ContactData") or {}
    params.setdefault("contact_id", contact.get("ContactId", ""))
    if not params.get("member_id"):
        params["member_id"] = (contact.get("Attributes") or {}).get("s1_member_id", "")
    out = OPS[op](params)
    print({"connect_op": op, "keys": sorted(out)})
    return _flat(_clean(out))


# ---------- member portal page (GET /) ----------
PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Member portal (demo)</title><style>
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f4f6f9;color:#1b2430}
header{background:#00539b;color:#fff;padding:14px 20px;display:flex;align-items:center;gap:12px}
header b{font-size:20px} header span{opacity:.85;font-size:14px}
.badge{margin-left:auto;background:#ffcc00;color:#1b2430;border-radius:4px;padding:3px 8px;font-size:12px;font-weight:600}
main{max-width:880px;margin:24px auto;padding:0 16px}
.card{background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
h1{font-size:22px;margin:0 0 6px} p{line-height:1.5}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.tile{border:1px solid #dfe5ec;border-radius:8px;padding:14px} .tile b{display:block;margin-bottom:4px}
footer{text-align:center;font-size:12px;color:#66727f;padding:20px}
</style></head><body>
<header><b>CAA Club Group</b><span>Member portal</span><div class="badge">DEMO - synthetic data</div></header>
<main>
<div class="card"><h1>Welcome back</h1><p>Need roadside help? Tap the chat button at the bottom right. Our assistant can
check your membership, find your location and arrange a tow. You can ask for a roadside agent at any time.</p></div>
<div class="grid">
<div class="tile"><b>Roadside assistance</b>Flat tire, battery boost, lockout, fuel delivery, towing.</div>
<div class="tile"><b>My membership</b>Plan, associates, renewal.</div>
<div class="tile"><b>Insurance</b>Home and auto policies, payments.</div>
</div></main>
<footer>Demonstration only. Not a real member service. No real member data.</footer>
__WIDGET__
</body></html>"""

WIDGET = """<script type="text/javascript">
(function(w,d,x,id){s=d.createElement('script');s.src='__SRC__';s.async=1;s.id=id;
d.getElementsByTagName('head')[0].appendChild(s);w[x]=w[x]||function(){(w[x].ac=w[x].ac||[]).push(arguments)};})
(window,document,'amazon_connect','__WIDGET_ID__');
amazon_connect('styles',{iconType:'CHAT',openChat:{color:'#ffffff',backgroundColor:'#00539b'},closeChat:{color:'#ffffff',backgroundColor:'#00539b'}});
amazon_connect('snippetId','__SNIPPET_ID__');
amazon_connect('supportedMessagingContentTypes',['text/plain','text/markdown','application/vnd.amazonaws.connect.message.interactive','application/vnd.amazonaws.connect.message.interactive.response']);
</script>"""


def _page():
    wid, snip, src = os.environ.get("WIDGET_ID", ""), os.environ.get("WIDGET_SNIPPET_ID", ""), os.environ.get("WIDGET_SRC", "")
    w = (WIDGET.replace("__SRC__", src).replace("__WIDGET_ID__", wid).replace("__SNIPPET_ID__", snip)
         if wid and snip and src else "<!-- chat widget not configured yet -->")
    return {"statusCode": 200, "headers": {"content-type": "text/html; charset=utf-8", "cache-control": "no-store"},
            "body": PAGE.replace("__WIDGET__", w)}


# ---------- Function URL ----------
def _find_contact_id(ctx, depth=0):
    """ACXD sends the context variable contact_id (= the Connect contact id) inside nlx_context."""
    if depth > 4 or ctx is None:
        return ""
    if isinstance(ctx, dict):
        for k in ("contact_id", "contactId", "ContactId", "InitialContactId"):
            v = ctx.get(k)
            if isinstance(v, str) and _UUID.fullmatch(v.strip()):
                return v.strip()
        for v in ctx.values():
            f = _find_contact_id(v, depth + 1)
            if f:
                return f
    elif isinstance(ctx, list):
        for v in ctx:
            f = _find_contact_id(v, depth + 1)
            if f:
                return f
    return ""


def _fields(body):
    flat = {}
    if isinstance(body, dict):
        for w in ("context", "data", "request", "payload"):
            if isinstance(body.get(w), dict):
                flat.update(body[w])
        flat.update({k: v for k, v in body.items() if not isinstance(v, (dict, list))})
        cid = _find_contact_id(body.get("nlx_context"))
        if cid:
            flat["contact_id"] = cid  # the contact id comes from Connect, never from the model
    return flat


def _resp(code, body):
    return {"statusCode": code, "headers": {"content-type": "application/json"},
            "body": json.dumps(_clean(body), default=str)}


def handler(event, _ctx):
    if "Details" in event:
        return connect_invoke(event)
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "POST")
    path = (event.get("rawPath") or "/").strip("/")
    if method == "GET" and path in ("", "portal"):
        return _page()
    if path == "health":
        members = len(SEED["tables"].get("MembersTable", []))
        return _resp(200, {"ok": True, "units": members, "key_configured": bool(os.environ.get("API_KEY"))})
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not hmac.compare_digest(headers.get("x-api-key", ""), os.environ.get("API_KEY", "")) or not os.environ.get("API_KEY"):
        return _resp(401, {"error": "unauthorized"})
    if path not in OPS:
        return _resp(404, {"error": "unknown_operation", "valid_options": sorted(OPS)})
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    try:
        body = _fields(json.loads(raw))
    except json.JSONDecodeError:
        return _resp(400, {"error": "bad_json", "message": "The request was not valid JSON."})
    print({"op": path, "keys": sorted(body), "contact_id_from_context": bool(body.get("contact_id"))})
    return _resp(200, OPS[path](body))
