"""CAA Scenario 1 (persistent cross-channel roadside assistance) - one Lambda, three entry points.

1. ACXD data requests (function URL, POST /<op>, header x-api-key):
     get_coverage          member tier -> roadside coverage (tow km, covered services)
     create_roadside_case  stores the AI intake for this chat (keyed by the Connect contact id) -> case id
     get_case              read the case back (contact_id)
2. Amazon Connect flow (Invoke Lambda, on ACXD escalation): returns the case as flat contact attributes
   (s1_*) so the live agent sees the AI intake without the member repeating anything.
3. GET / (function URL, no key): the member-portal demo page with the Amazon Connect chat widget.

Synthetic data only. The member record comes from the shared CRM table (Scenario 5 stack, ccg-s5-members).
"""
import datetime as dt
import hmac
import json
import os
import random
import re
from decimal import Decimal

CASES = MEMBERS = None  # DynamoDB tables (lazy; replaced by fakes in tests)

COVERAGE = {  # synthetic plan rules for the demo
    "Basic": {"tow_km": 10, "services": ["battery boost", "flat tire change", "lockout", "fuel delivery", "tow"]},
    "Plus": {"tow_km": 200, "services": ["battery boost", "flat tire change", "lockout", "fuel delivery", "tow",
                                         "extrication"]},
    "Premier": {"tow_km": 320, "services": ["battery boost", "flat tire change", "lockout", "fuel delivery", "tow",
                                            "extrication", "trip interruption"]},
}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _tables():
    global CASES, MEMBERS
    if CASES is None:
        import boto3
        ddb = boto3.resource("dynamodb")
        CASES, MEMBERS = ddb.Table(os.environ["CASES_TABLE"]), ddb.Table(os.environ["MEMBERS_TABLE"])
    return CASES, MEMBERS


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


def _member_id(raw):
    d = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return f"M-{d}" if d else ""


def _yes(v):
    return str(v).strip().lower() in ("true", "yes", "y", "1", "safe", "confirmed")


def _find_contact_id(ctx, depth=0):
    """ACXD sends the context variable contact_id (= Connect InitialContactId) inside nlx_context."""
    if depth > 4 or ctx is None:
        return ""
    if isinstance(ctx, dict):
        for k in ("contact_id", "contactId", "InitialContactId"):
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


def get_coverage(b):
    m = _tables()[1].get_item(Key={"member_id": _member_id(b.get("member_id") or b.get("membership_number"))}).get("Item")
    if not m:
        return {"found": False}
    tier = m.get("tier", "Basic")
    cov = COVERAGE.get(tier, COVERAGE["Basic"])
    return {"found": True, "member_id": m["member_id"], "tier": tier, "tow_km": cov["tow_km"],
            "covered_services": cov["services"], "tow_included_text": f"up to {cov['tow_km']} km of towing at no charge"}


def create_roadside_case(b):
    cid = str(b.get("contact_id") or "").strip()
    if not _UUID.fullmatch(cid):
        return {"created": False, "reason": "no_contact_id"}
    mid = _member_id(b.get("member_id"))
    if not mid:
        return {"created": False, "missing": "member_id"}
    safe = _yes(b.get("safe", ""))
    case_id = f"RSA-{random.randint(100000, 999999)}"
    now = dt.datetime.now(dt.timezone.utc)
    item = {
        "contact_id": cid, "case_id": case_id, "member_id": mid,
        "member_name": str(b.get("member_name") or "")[:80], "tier": str(b.get("tier") or "")[:20],
        "vehicle": str(b.get("vehicle") or "")[:80], "verified": _yes(b.get("verified", "")),
        "auth_level": "service (membership number + vehicle)",
        "location": str(b.get("location") or "")[:200], "service_type": str(b.get("service_type") or "")[:60],
        "safe": safe, "spare": str(b.get("spare") or "")[:40], "passengers": str(b.get("passengers") or "")[:80],
        "photo_received": _yes(b.get("photo_received", "")), "concern": str(b.get("concern") or "")[:200],
        "priority": "Standard" if safe else "High", "status": "intake_complete", "channel": "chat",
        "created_at": now.isoformat(timespec="seconds"), "expires_at": int(now.timestamp()) + 7 * 86400,
    }
    _tables()[0].put_item(Item=item)
    return {"created": True, "case_id": case_id, "priority": item["priority"]}


def get_case(b):
    cid = str(b.get("contact_id") or "").strip()
    r = _tables()[0].get_item(Key={"contact_id": cid}).get("Item") if cid else None
    if not r:
        return {"found": False}
    return dict({k: v for k, v in r.items() if k != "expires_at"}, found=True)


def _summary(r):
    return (f"{r.get('member_name', '')} ({r.get('tier', '')}, verified: {'yes' if r.get('verified') else 'no'}) - "
            f"{r.get('service_type', '')}; {r.get('vehicle', '')}; at {r.get('location', '')}; "
            f"safe: {'yes' if r.get('safe') else 'NO'}; spare: {r.get('spare', '')}; "
            f"photo: {'yes' if r.get('photo_received') else 'no'}. {r.get('concern', '')}").strip()


def connect_attributes(event):
    """Called by the Connect chat flow on ACXD escalation: case -> flat string map (contact attributes)."""
    cd = (event.get("Details") or {}).get("ContactData") or {}
    for cid in (cd.get("InitialContactId"), cd.get("ContactId")):
        r = _tables()[0].get_item(Key={"contact_id": cid}).get("Item") if cid else None
        if r:
            yn = lambda v: "Yes" if v else "No"  # noqa: E731
            return {"found": "true", "s1_case_id": r["case_id"], "s1_member_id": r["member_id"],
                    "s1_member_name": r.get("member_name", ""), "s1_tier": r.get("tier", ""),
                    "s1_vehicle": r.get("vehicle", ""), "s1_verified": "Verified (service level)" if r.get("verified")
                    else "Not verified", "s1_location": r.get("location", ""), "s1_service": r.get("service_type", ""),
                    "s1_safe": yn(r.get("safe")), "s1_spare": r.get("spare", ""), "s1_photo": yn(r.get("photo_received")),
                    "s1_priority": r.get("priority", ""), "s1_summary": _summary(r)[:1000]}
    return {"found": "false"}


OPS = {f.__name__: f for f in (get_coverage, create_roadside_case, get_case)}

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
verify your membership, find you and arrange a tow, and connect you to a roadside agent at any time.</p></div>
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


def _fields(body):
    flat = {}
    if isinstance(body, dict):
        for w in ("context", "data", "request", "payload"):
            if isinstance(body.get(w), dict):
                flat.update(body[w])
        flat.update({k: v for k, v in body.items() if not isinstance(v, (dict, list))})
        cid = _find_contact_id(body.get("nlx_context"))
        if cid:
            flat["contact_id"] = cid  # the contact id comes from the phone/chat system, never from the model
    return flat


def _resp(code, body):
    return {"statusCode": code, "headers": {"content-type": "application/json"},
            "body": json.dumps(_clean(body), default=str)}


def handler(event, _ctx):
    if "Details" in event:  # Amazon Connect flow
        return connect_attributes(event)
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "POST")
    path = (event.get("rawPath") or "/").strip("/")
    if method == "GET" and path in ("", "portal"):
        return _page()
    if path == "health":
        return _resp(200, {"ok": True, "units": len(COVERAGE), "key_configured": bool(os.environ.get("API_KEY"))})
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not hmac.compare_digest(headers.get("x-api-key", ""), os.environ["API_KEY"]):
        return _resp(401, {"error": "unauthorized"})
    if path not in OPS:
        return _resp(404, {"error": "unknown operation", "operations": sorted(OPS)})
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    body = _fields(json.loads(raw))
    print({"op": path, "keys": sorted(body), "contact_id_source": "context" if body.get("contact_id") else "none"})
    return _resp(200, OPS[path](body))
