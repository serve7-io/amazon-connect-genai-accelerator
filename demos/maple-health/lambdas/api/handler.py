"""Maple Health Network (fictional Canadian clinic network): patient chat API. Synthetic data only.

Two event shapes:
1. Function URL, from ACXD data requests: POST /<op>, header x-api-key, JSON body. Returns JSON.
   GET /inbox (no key) returns the simulated email inbox shown on the demo page (only @example.com demo addresses).
   POST /health (no key) is used by the smoke test.
2. Amazon Connect Invoke Lambda, from the chat flow after an escalation: op in Details.Parameters.op.
   Returns a flat string map (Connect reads it as $.External.<key>).

Identity: email one-time code. The chat session is the Connect contact id that ACXD sends in nlx_context
(context variable contact_id). It is never taken from the model. The code itself never goes back to the model:
send_code stores only a keyed hash and "emails" the code to the simulated inbox.

Operations (every response has a ready-to-say "message"; errors add "error" and, where useful, "valid_options"):
  send_code        email -> a 6-digit code to that address (generic answer, never says whether the email exists)
  verify_code      code -> verified true/false (5 tries, 10 minutes)
  clinic_info      clinics, address, hours, phone, languages, services (no verification needed)
  find_slots       service, optional clinic, date_from, language -> open slots (no verification needed)
  my_appointments  the verified patient's upcoming appointments
  book             slot_id, optional replaces_appointment_id (reschedule) -> appointment
  cancel           appointment_id -> cancelled
  intake           (Connect invoke, after escalation) who the patient is, verified or not, what happened in the chat
"""
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
from decimal import Decimal

TABLE_ENV = {"ClinicsTable": "CLINICS_TABLE", "SlotsTable": "SLOTS_TABLE", "PatientsTable": "PATIENTS_TABLE",
             "AppointmentsTable": "APPOINTMENTS_TABLE", "SessionsTable": "SESSIONS_TABLE", "InboxTable": "INBOX_TABLE"}
SERVICES = {"family_doctor": ("family doctor visit", "rendez-vous avec un médecin de famille"),
            "nurse_practitioner": ("nurse practitioner visit", "rendez-vous avec une infirmière praticienne"),
            "blood_test": ("blood test", "prise de sang"),
            "flu_vaccine": ("flu vaccine", "vaccin contre la grippe"),
            "physiotherapy": ("physiotherapy", "physiothérapie")}
CODE_TTL_S = 600
MAX_TRIES = 5
MAX_SENDS = 3
SESSION_TTL_S = 24 * 3600
INBOX_WINDOW_S = 1800
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


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

    def scan(self, table):
        t, items, kw = self.t[table], [], {}
        while True:
            p = t.scan(**kw)
            items += p.get("Items", [])
            if "LastEvaluatedKey" not in p:
                return items
            kw["ExclusiveStartKey"] = p["LastEvaluatedKey"]

    def query(self, table, attr, value, index=None):
        kw = {"KeyConditionExpression": self.Key(attr).eq(value)}
        if index:
            kw["IndexName"] = index
        return self.t[table].query(**kw).get("Items", [])


DB = None


def db():
    global DB
    if DB is None:
        DB = Db()
    return DB


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


def _today():
    t = os.environ.get("TODAY")
    if t:
        return dt.date.fromisoformat(t)
    return (_now() - dt.timedelta(hours=4)).date()   # Ottawa / Gatineau (EDT); good enough for a demo


def _fr(b):
    return str(b.get("language") or "").lower().startswith("fr")


def _svc(key, fr=False):
    names = SERVICES.get(key)
    return names[1 if fr else 0] if names else key


def _mask(email):
    user, _, dom = email.partition("@")
    return (user[:1] + "***@" + dom) if user else email


def _hash(code, cid):
    pepper = (os.environ.get("API_KEY") or "demo").encode()
    return hmac.new(pepper, f"{cid}:{code}".encode(), hashlib.sha256).hexdigest()


# ---------- session (keyed by the Connect contact id from nlx_context) ----------
def _session(cid):
    return (db().get("SessionsTable", {"contact_id": cid}) or {"contact_id": cid}) if cid else None


def _save(sess):
    sess["expires_at"] = int(_now().timestamp()) + SESSION_TTL_S   # DynamoDB TTL
    db().put("SessionsTable", sess)


def _log(sess, line):
    hist = list(sess.get("history") or [])[-7:]
    hist.append(line)
    sess["history"] = hist


NO_SESSION = {"error": "no_session",
              "message": "I can't verify your identity in this chat right now. A team member can help you instead."}


def _verified(b):
    """(session, patient) when this chat is verified, else (session or None, None)."""
    sess = _session(b.get("_cid"))
    if not sess or not sess.get("verified"):
        return sess, None
    return sess, db().get("PatientsTable", {"patient_id": sess["patient_id"]})


NOT_VERIFIED = {"error": "not_verified",
                "message": "I need to confirm your identity first. What is the email address in your patient file?"}


# ---------- operations ----------
def send_code(b):
    sess = _session(b.get("_cid"))
    if sess is None:
        return NO_SESSION
    email = str(b.get("email") or "").strip().lower()
    if not _EMAIL.match(email):
        return {"sent": False, "error": "invalid_email", "message": "That doesn't look like an email address. Could you type it again?"}
    if int(sess.get("sends", 0)) >= MAX_SENDS:
        return {"sent": False, "error": "too_many_codes",
                "message": "I've already sent the maximum number of codes for this chat. A team member can help you verify."}
    hits = db().query("PatientsTable", "email_key", email, index="email-index")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    sess.update({"sends": int(sess.get("sends", 0)) + 1, "tries": 0, "email": email,
                 "code_expires": int(_now().timestamp()) + CODE_TTL_S})
    if hits:
        p = hits[0]
        sess["pending_patient"] = p["patient_id"]
        sess["code_hash"] = _hash(code, sess["contact_id"])
        _deliver(p, code)
    else:
        sess["pending_patient"] = ""
        sess["code_hash"] = _hash(secrets.token_hex(8), sess["contact_id"])   # nothing can match
    _log(sess, f"code requested for {_mask(email)}")
    _save(sess)
    return {"sent": True, "to": _mask(email), "expires_in_minutes": CODE_TTL_S // 60,
            "message": f"If {_mask(email)} is in your patient file, a 6-digit code is on its way. It is valid for 10 minutes. "
                       "Please type it here when you have it."}


def _deliver(p, code):
    """The simulated inbox: what a real deployment would send with Amazon SES."""
    fr = p.get("preferred_language") == "fr"
    subject = "Votre code Maple Santé" if fr else "Your Maple Health code"
    body = (f"Bonjour {p['first_name']}, votre code de vérification est {code}. Il expire dans 10 minutes. "
            "Ne le communiquez à personne d'autre que l'assistant de clavardage."
            if fr else
            f"Hi {p['first_name']}, your verification code is {code}. It expires in 10 minutes. "
            "Only enter it in the Maple Health chat.")
    now = _now()
    db().put("InboxTable", {"msg_id": secrets.token_hex(8), "to": p["email"], "subject": subject, "body": body,
                            "sent_at": now.isoformat(timespec="seconds"),
                            "expires_at": int(now.timestamp()) + SESSION_TTL_S})


def verify_code(b):
    sess = _session(b.get("_cid"))
    if sess is None:
        return NO_SESSION
    if sess.get("verified"):
        return {"verified": True, "first_name": sess.get("first_name", ""), "message": "You're already verified in this chat."}
    code = re.sub(r"\D", "", str(b.get("code") or ""))
    if not sess.get("code_hash"):
        return {"verified": False, "error": "no_code", "message": "I haven't sent a code yet. What is the email address in your patient file?"}
    if int(_now().timestamp()) > int(sess.get("code_expires", 0)):
        return {"verified": False, "error": "code_expired", "message": "That code has expired. I can send a new one to your email."}
    tries = int(sess.get("tries", 0))
    if tries >= MAX_TRIES:
        return {"verified": False, "error": "locked", "message": "Too many incorrect codes. A team member can help you verify."}
    ok = len(code) == 6 and hmac.compare_digest(_hash(code, sess["contact_id"]), sess["code_hash"]) and sess.get("pending_patient")
    if not ok:
        sess["tries"] = tries + 1
        _log(sess, "wrong code entered")
        _save(sess)
        left = MAX_TRIES - sess["tries"]
        return {"verified": False, "error": "wrong_code", "tries_left": left,
                "message": f"That code doesn't match. Please check the email and try again ({left} tries left)."
                if left else "That code doesn't match, and that was the last try. A team member can help you verify."}
    p = db().get("PatientsTable", {"patient_id": sess["pending_patient"]})
    sess.update({"verified": True, "patient_id": p["patient_id"], "first_name": p["first_name"],
                 "patient_name": f"{p['first_name']} {p['last_name']}", "verified_at": _now().isoformat(timespec="seconds"),
                 "preferred_language": p.get("preferred_language", "en")})
    sess.pop("code_hash", None)
    _log(sess, "identity verified by email code")
    _save(sess)
    return {"verified": True, "first_name": p["first_name"], "preferred_language": p.get("preferred_language", "en"),
            "message": f"Thank you, {p['first_name']}. You're verified."}


def _clinics():
    return {c["clinic_id"]: c for c in db().scan("ClinicsTable")}


def clinic_info(b):
    fr, clinics = _fr(b), _clinics()
    services = {}
    for s in db().scan("SlotsTable"):
        services.setdefault(s["clinic_id"], set()).add(s["service"])
    out = []
    for cid, c in sorted(clinics.items()):
        out.append({"clinic_id": cid, "name": c["name_fr"] if fr else c["name"], "address": c["address"], "phone": c["phone"],
                    "hours": c["hours"], "languages": list(c.get("languages", [])),
                    "services": [_svc(s, fr) for s in sorted(services.get(cid, ()))],
                    "service_codes": sorted(services.get(cid, ()))})
    return {"clinics": out, "today": _today().isoformat(), "service_codes": sorted(SERVICES),
            "message": f"Maple Health has {len(out)} clinics."}


def _slot_view(s, clinics, fr):
    c = clinics.get(s["clinic_id"], {})
    return {"slot_id": s["slot_id"], "clinic": c.get("name_fr" if fr else "name", s["clinic_id"]), "clinic_id": s["clinic_id"],
            "service": _svc(s["service"], fr), "date": s["slot_date"], "time": s["time"], "provider": s["provider"],
            "languages": list(s.get("languages", [])), "duration_min": _num(s.get("duration_min", 30))}


def find_slots(b):
    fr = _fr(b)
    svc = str(b.get("service") or "").strip().lower()
    if svc not in SERVICES:
        return {"slots": [], "error": "unknown_service", "valid_options": sorted(SERVICES),
                "message": "Which kind of appointment? Family doctor, nurse practitioner, blood test, flu vaccine or physiotherapy."}
    today = _today()
    try:
        start = dt.date.fromisoformat(str(b.get("date_from"))) if b.get("date_from") else today
    except ValueError:
        return {"slots": [], "error": "bad_date", "message": "Please give the date as YYYY-MM-DD."}
    start = max(start, today)
    end = start + dt.timedelta(days=14)
    clinic = str(b.get("clinic_id") or b.get("clinic") or "").strip().upper()[:3]
    lang = str(b.get("language_needed") or "").strip().lower()[:2]
    clinics = _clinics()
    rows = [s for s in db().scan("SlotsTable") if s["service"] == svc and s.get("status") == "open"
            and start.isoformat() <= s["slot_date"] <= end.isoformat()
            and (not clinic or s["clinic_id"] == clinic) and (not lang or lang in s.get("languages", []))
            and not (s["slot_date"] == today.isoformat())]
    rows.sort(key=lambda s: (s["slot_date"], s["time"]))
    found = [_slot_view(s, clinics, fr) for s in rows[:6]]
    if not found:
        return {"slots": [], "today": today.isoformat(), "error": "no_slots",
                "message": f"There are no open {_svc(svc, fr)} appointments for those choices in the next two weeks. "
                           "Another clinic or date might work."}
    return {"slots": found, "today": today.isoformat(), "message": f"I found {len(found)} open times."}


def _appt_view(a, clinics, fr):
    c = clinics.get(a["clinic_id"], {})
    return {"appointment_id": a["appointment_id"], "clinic": c.get("name_fr" if fr else "name", a["clinic_id"]),
            "address": c.get("address", ""), "service": _svc(a["service"], fr), "date": a["appt_date"], "time": a["time"],
            "provider": a["provider"], "status": a["status"]}


def _upcoming(patient_id):
    today = _today().isoformat()
    rows = [a for a in db().scan("AppointmentsTable")
            if a["patient_id"] == patient_id and a.get("status") == "booked" and a["appt_date"] >= today]
    return sorted(rows, key=lambda a: (a["appt_date"], a["time"]))


def my_appointments(b):
    sess, p = _verified(b)
    if not p:
        return NOT_VERIFIED
    clinics, fr = _clinics(), _fr(b)
    rows = [_appt_view(a, clinics, fr) for a in _upcoming(p["patient_id"])]
    return {"appointments": rows, "message": f"You have {len(rows)} upcoming appointment(s)." if rows
            else "You have no upcoming appointments."}


def book(b):
    sess, p = _verified(b)
    if not p:
        return NOT_VERIFIED
    slot = db().get("SlotsTable", {"slot_id": str(b.get("slot_id") or "").strip().upper()})
    if not slot:
        return {"booked": False, "error": "unknown_slot", "message": "I couldn't find that time. Let me look up open times again."}
    if slot.get("status") != "open":
        return {"booked": False, "error": "slot_taken", "message": "Sorry, that time was just taken. Let me find another one."}
    old = None
    rid = str(b.get("replaces_appointment_id") or "").strip().upper()
    if rid:
        old = db().get("AppointmentsTable", {"appointment_id": rid})
        if not old or old["patient_id"] != p["patient_id"] or old.get("status") != "booked":
            return {"booked": False, "error": "appointment_not_found",
                    "message": "I couldn't find that appointment in your file."}
    slot["status"] = "booked"
    db().put("SlotsTable", slot)
    appt = {"appointment_id": f"MH-{secrets.randbelow(90000) + 10000}", "patient_id": p["patient_id"], "slot_id": slot["slot_id"],
            "clinic_id": slot["clinic_id"], "service": slot["service"], "appt_date": slot["slot_date"], "time": slot["time"],
            "provider": slot["provider"], "status": "booked", "booked_at": _now().isoformat(timespec="seconds"),
            "booked_via": "chat"}
    db().put("AppointmentsTable", appt)
    if old:
        _release(old, "rescheduled")
    fr, clinics = _fr(b), _clinics()
    view = _appt_view(appt, clinics, fr)
    _log(sess, f"{'rescheduled' if old else 'booked'} {appt['service']} {appt['appt_date']} {appt['time']} ({appt['appointment_id']})")
    _save(sess)
    return {"booked": True, "rescheduled_from": old["appointment_id"] if old else "", **view,
            "message": f"Booked: {view['service']} on {view['date']} at {view['time']} with {view['provider']}, {view['clinic']}. "
                       f"Your appointment number is {appt['appointment_id']}."}


def _release(appt, status):
    appt["status"] = status
    db().put("AppointmentsTable", appt)
    slot = db().get("SlotsTable", {"slot_id": appt["slot_id"]})
    if slot:
        slot["status"] = "open"
        db().put("SlotsTable", slot)


def cancel(b):
    sess, p = _verified(b)
    if not p:
        return NOT_VERIFIED
    a = db().get("AppointmentsTable", {"appointment_id": str(b.get("appointment_id") or "").strip().upper()})
    if not a or a["patient_id"] != p["patient_id"] or a.get("status") != "booked":
        return {"cancelled": False, "error": "appointment_not_found", "message": "I couldn't find that appointment in your file."}
    _release(a, "cancelled")
    _log(sess, f"cancelled {a['appointment_id']} ({a['service']} {a['appt_date']})")
    _save(sess)
    return {"cancelled": True, "appointment_id": a["appointment_id"],
            "message": f"Appointment {a['appointment_id']} on {a['appt_date']} at {a['time']} is cancelled."}


def intake(b):
    cid = str(b.get("contact_id") or "").strip()
    sess = db().get("SessionsTable", {"contact_id": cid}) if cid else None
    if not sess:
        return {"found": False, "verified": "No", "patient_name": "Unknown", "summary": "The patient was not identified in the chat.",
                "message": "No session for this contact."}
    verified = bool(sess.get("verified"))
    nxt = ""
    if verified:
        up = _upcoming(sess["patient_id"])
        if up:
            a = up[0]
            nxt = f"{_svc(a['service'])} {a['appt_date']} {a['time']} ({a['appointment_id']})"
    hist = "; ".join(sess.get("history") or [])
    name = sess.get("patient_name", "") if verified else "Not verified"
    summary = (f"{name} ({sess.get('patient_id', '')}), identity verified by email code at {sess.get('verified_at', '')}. "
               if verified else "Identity NOT verified in the chat. ") + (f"Chat: {hist}." if hist else "")
    return {"found": True, "verified": "Yes (email code)" if verified else "No", "patient_name": name,
            "patient_id": sess.get("patient_id", "") if verified else "", "language": sess.get("preferred_language", ""),
            "next_appointment": nxt, "summary": summary[:900], "message": "Session loaded."}


OPS = {f.__name__: f for f in (send_code, verify_code, clinic_info, find_slots, my_appointments, book, cancel)}


# ---------- simulated inbox for the demo page (GET /inbox, public, example.com only) ----------
def inbox():
    cutoff = (_now() - dt.timedelta(seconds=INBOX_WINDOW_S)).isoformat(timespec="seconds")
    rows = [m for m in db().scan("InboxTable") if str(m.get("to", "")).endswith("@example.com") and m.get("sent_at", "") >= cutoff]
    rows.sort(key=lambda m: m["sent_at"], reverse=True)
    return {"messages": [{k: m[k] for k in ("to", "subject", "body", "sent_at")} for m in rows[:8]]}


# ---------- Amazon Connect (Invoke Lambda) ----------
def _flat(d):
    out = {}
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            continue
        out[k] = ("true" if v else "false") if isinstance(v, bool) else ("" if v is None else str(_num(v)))
    return out


def connect_invoke(event):
    det = event.get("Details") or {}
    params = dict(det.get("Parameters") or {})
    op = str(params.pop("op", "")).strip()
    if op != "intake":
        return {"error": "unknown_operation", "message": ""}
    params.setdefault("contact_id", (det.get("ContactData") or {}).get("ContactId", ""))
    out = intake(params)
    print({"connect_op": op, "found": out.get("found")})
    return _flat(_clean(out))


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
        flat.pop("_cid", None)
        flat["_cid"] = _find_contact_id(body.get("nlx_context"))   # the session comes from Connect, never from the model
    return flat


def _resp(code, body):
    headers = {"content-type": "application/json", "cache-control": "no-store"}
    return {"statusCode": code, "headers": headers, "body": json.dumps(_clean(body), default=str, ensure_ascii=False)}


def handler(event, _ctx):
    if "Details" in event:
        return connect_invoke(event)
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "POST")
    path = (event.get("rawPath") or "/").strip("/")
    if path == "health":
        n = len(db().scan("ClinicsTable"))
        return _resp(200, {"ok": True, "units": n, "key_configured": bool(os.environ.get("API_KEY"))})
    if path == "inbox" and method == "GET":
        return _resp(200, inbox())
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not os.environ.get("API_KEY") or not hmac.compare_digest(headers.get("x-api-key", ""), os.environ["API_KEY"]):
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
    out = OPS[path](body)
    print({"op": path, "keys": sorted(k for k in body if k != "code"), "session": bool(body.get("_cid")),
           "error": out.get("error", "")})
    return _resp(200, out)
