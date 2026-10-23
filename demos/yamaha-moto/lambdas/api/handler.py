"""Yamaha moto rental demo: HTTP API for the ACXD chat agent's data requests (Lambda function URL).

POST /<operation>, JSON body, header x-api-key. Synthetic data only (invented fleet, prices and reservations).
Operations:
  list_bikes          models with cc, power, licence, price per day, deposit (optional filters)
  check_availability  which models/units are free for a date range
  quote               price for one model and date range (multi-day discount, deposit)
  create_reservation  books a free unit, checks the licence category, returns a confirmation number
  get_reservation     look up a reservation by confirmation number
  cancel_reservation  cancel by confirmation number + phone
Dates are YYYY-MM-DD. A rental runs from the pickup date to the return date (same day = 1 day).
"""
import datetime as dt
import hmac
import json
import os
import random
from decimal import Decimal

FLEET = RES = None  # DynamoDB tables (created lazily; replaced by fakes in tests)

LICENCE_RANK = {"AM": 0, "A1": 1, "A2": 2, "A": 3}
DISCOUNTS = ((7, Decimal("0.15")), (3, Decimal("0.10")))  # 7+ days 15 %, 3-6 days 10 %
KM_PER_DAY = 250


def _tables():
    global FLEET, RES
    if FLEET is None:
        import boto3
        ddb = boto3.resource("dynamodb")
        FLEET, RES = ddb.Table(os.environ["FLEET_TABLE"]), ddb.Table(os.environ["RESERVATIONS_TABLE"])
    return FLEET, RES


def _today():
    return dt.date.fromisoformat(os.environ["TODAY"]) if os.environ.get("TODAY") else dt.date.today()


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


def _scan(table):
    items, kw = [], {}
    while True:
        page = table.scan(**kw)
        items += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return items
        kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _key(s):
    """'MT-07', 'mt07', 'Yamaha MT 07', 'tenere 700' -> 'mt07' / 'tenere700' for matching."""
    s = str(s or "").lower().replace("é", "e").replace("yamaha", "")
    return "".join(ch for ch in s if ch.isalnum())


def _model_rows(model=None):
    rows = [r for r in _scan(_tables()[0])]
    if model:
        k = _key(model)
        exact = [r for r in rows if _key(r["model"]) == k or _key(r["model_id"]) == k]
        rows = exact or [r for r in rows if k and (k in _key(r["model"]) or _key(r["model"]) in k)]
    return rows


def _dates(b):
    """-> (start, end_exclusive, days) or raises ValueError with a message the agent can relay."""
    try:
        start = dt.date.fromisoformat(str(b["start_date"]).strip()[:10])
        end_raw = str(b.get("end_date") or "").strip()[:10]
        end = dt.date.fromisoformat(end_raw) if end_raw else start
    except (KeyError, ValueError):
        raise ValueError("dates must be YYYY-MM-DD (start_date, end_date)")
    if start < _today():
        raise ValueError(f"start_date {start} is in the past (today is {_today()})")
    if end < start:
        raise ValueError("end_date is before start_date")
    days = max(1, (end - start).days)
    return start, start + dt.timedelta(days=days), days


def _busy_units(start, end_x):
    busy = set()
    for r in _scan(_tables()[1]):
        if r.get("status") != "confirmed":
            continue
        rs, re_ = dt.date.fromisoformat(r["start_date"]), dt.date.fromisoformat(r["end_exclusive"])
        if rs < end_x and start < re_:
            busy.add(r["unit_id"])
    return busy


def _model_view(r):
    return {"model": r["model"], "model_id": r["model_id"], "type": r["type"], "cc": r["cc"], "power_kw": r["power_kw"],
            "licence": r["licence"], "price_per_day_eur": r["price_per_day"], "deposit_eur": r["deposit"],
            "seat_height_mm": r.get("seat_height_mm"), "highlights": r.get("highlights", "")}


def _models(rows):
    out = {}
    for r in rows:
        out.setdefault(r["model_id"], _model_view(r))
    return out


def list_bikes(b):
    rows = _model_rows(b.get("model"))
    lic = str(b.get("licence") or "").upper().replace(" ", "")
    models = list(_models(rows).values())
    if b.get("type"):
        models = [m for m in models if str(b["type"]).lower() in m["type"].lower()]
    if lic in LICENCE_RANK:
        models = [m for m in models if LICENCE_RANK[m["licence"]] <= LICENCE_RANK[lic]]
    if b.get("max_price_per_day"):
        models = [m for m in models if m["price_per_day_eur"] <= Decimal(str(b["max_price_per_day"]))]
    if b.get("min_cc"):
        models = [m for m in models if m["cc"] >= int(b["min_cc"])]
    if b.get("max_cc"):
        models = [m for m in models if m["cc"] <= int(b["max_cc"])]
    models.sort(key=lambda m: (m["price_per_day_eur"], m["model"]))
    units = {}
    for r in rows:
        units[r["model_id"]] = units.get(r["model_id"], 0) + (0 if r.get("maintenance") else 1)
    for m in models:
        m["units_in_fleet"] = units.get(m["model_id"], 0)
    return {"today": _today().isoformat(), "location": "Belgrade", "currency": "EUR", "count": len(models),
            "bikes": models, "rental_terms": _terms()}


def _terms():
    return {"discounts": "3-6 days 10% off, 7+ days 15% off", "km_included_per_day": KM_PER_DAY,
            "included": "helmet, gloves, third-party insurance", "deposit": "card hold, released on return",
            "pickup": "Belgrade, 09:00-18:00", "licence_rule": "AM < A1 < A2 < A"}


def check_availability(b):
    start, end_x, days = _dates(b)
    rows = _model_rows(b.get("model"))
    if b.get("model") and not rows:
        return {"found": False, "message": f"no model matches '{b['model']}'"}
    busy = _busy_units(start, end_x)
    models = _models(rows)
    free, booked = {}, {}
    for r in rows:
        if r.get("maintenance"):
            continue
        (booked if r["unit_id"] in busy else free).setdefault(r["model_id"], []).append(r["unit_id"])
    available = [dict(models[mid], free_units=len(u)) for mid, u in free.items()]
    available.sort(key=lambda m: (m["price_per_day_eur"], m["model"]))
    unavailable = sorted(models[mid]["model"] for mid in models if mid not in free)
    return {"start_date": start.isoformat(), "return_date": end_x.isoformat(), "days": days,
            "available": available, "not_available": unavailable}


def _price(daily, days):
    daily = Decimal(str(daily))
    pct = next((p for d, p in DISCOUNTS if days >= d), Decimal("0"))
    gross = daily * days
    discount = (gross * pct).quantize(Decimal("0.01"))
    return {"days": days, "price_per_day_eur": daily, "subtotal_eur": gross, "discount_percent": int(pct * 100),
            "discount_eur": discount, "total_eur": gross - discount, "km_included": KM_PER_DAY * days}


def quote(b):
    rows = _model_rows(b.get("model"))
    if not rows:
        return {"found": False, "message": f"no model matches '{b.get('model')}'"}
    start, end_x, days = _dates(b)
    m = _model_view(rows[0])
    return dict(_price(m["price_per_day_eur"], days), found=True, model=m["model"], deposit_eur=m["deposit_eur"],
                start_date=start.isoformat(), licence=m["licence"])


def create_reservation(b):
    for f in ("model", "start_date", "customer_name", "phone", "licence"):
        if not str(b.get(f) or "").strip():
            return {"created": False, "missing": f}
    lic = str(b["licence"]).upper().replace(" ", "")
    if lic not in LICENCE_RANK:
        return {"created": False, "reason": "unknown_licence", "message": "licence must be AM, A1, A2 or A"}
    rows = [r for r in _model_rows(b["model"]) if not r.get("maintenance")]
    if not rows:
        return {"created": False, "reason": "unknown_model"}
    m = _model_view(rows[0])
    if LICENCE_RANK[lic] < LICENCE_RANK[m["licence"]]:
        return {"created": False, "reason": "licence_too_low", "required_licence": m["licence"], "model": m["model"]}
    start, end_x, days = _dates(b)
    busy = _busy_units(start, end_x)
    unit = next((r["unit_id"] for r in sorted(rows, key=lambda r: r["unit_id"]) if r["unit_id"] not in busy), None)
    if not unit:
        return {"created": False, "reason": "not_available", "model": m["model"]}
    p = _price(m["price_per_day_eur"], days)
    table = _tables()[1]
    for _ in range(5):
        conf = f"YAM-{random.randint(10000, 99999)}"
        item = {"confirmation": conf, "unit_id": unit, "model": m["model"], "model_id": m["model_id"],
                "start_date": start.isoformat(), "end_exclusive": end_x.isoformat(), "days": days,
                "customer_name": str(b["customer_name"])[:80], "phone": str(b["phone"])[:30], "licence": lic,
                "total_eur": p["total_eur"], "deposit_eur": m["deposit_eur"], "status": "confirmed",
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "channel": "chat"}
        try:
            table.put_item(Item=item, ConditionExpression="attribute_not_exists(confirmation)")
            break
        except Exception as e:  # noqa: BLE001 - only the conditional-check failure is retried
            if "ConditionalCheckFailed" not in type(e).__name__ + str(e):
                raise
    return dict(p, created=True, confirmation=conf, model=m["model"], unit_id=unit, start_date=start.isoformat(),
                return_date=(start + dt.timedelta(days=days)).isoformat(), deposit_eur=m["deposit_eur"],
                pickup="Belgrade, 09:00-18:00, bring your licence and ID")


def get_reservation(b):
    r = _tables()[1].get_item(Key={"confirmation": str(b.get("confirmation", "")).strip().upper()}).get("Item")
    if not r:
        return {"found": False}
    return {"found": True, "confirmation": r["confirmation"], "model": r["model"], "start_date": r["start_date"],
            "return_date": r["end_exclusive"], "days": r["days"], "total_eur": r["total_eur"],
            "status": r["status"], "customer_first_name": r["customer_name"].split()[0]}


def _digits(s):
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def cancel_reservation(b):
    conf = str(b.get("confirmation", "")).strip().upper()
    table = _tables()[1]
    r = table.get_item(Key={"confirmation": conf}).get("Item")
    if not r or _digits(r["phone"])[-6:] != _digits(b.get("phone"))[-6:]:
        return {"cancelled": False, "reason": "not_found"}   # never reveal whether the number or the phone was wrong
    if r["status"] == "cancelled":
        return {"cancelled": True, "already": True, "confirmation": conf}
    table.update_item(Key={"confirmation": conf}, UpdateExpression="SET #s = :c",
                      ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":c": "cancelled"})
    return {"cancelled": True, "confirmation": conf, "model": r["model"], "start_date": r["start_date"]}


OPS = {f.__name__: f for f in (list_bikes, check_availability, quote, create_reservation, get_reservation,
                               cancel_reservation)}


def _fields(body):
    """ACXD may wrap our fields (payload/data/request/context); flatten, later wins."""
    flat = {}
    if isinstance(body, dict):
        for w in ("context", "data", "request", "payload"):
            if isinstance(body.get(w), dict):
                flat.update(body[w])
        flat.update({k: v for k, v in body.items() if not isinstance(v, (dict, list))})
    return flat


def _resp(code, body):
    return {"statusCode": code, "headers": {"content-type": "application/json"},
            "body": json.dumps(_clean(body), default=str)}


def handler(event, _ctx):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if (event.get("rawPath") or "").strip("/") == "health":   # no key needed: counts only, no data
        return _resp(200, {"ok": True, "units": len(_scan(_tables()[0])), "key_configured": bool(os.environ.get("API_KEY"))})
    if not hmac.compare_digest(headers.get("x-api-key", ""), os.environ["API_KEY"]):
        return _resp(401, {"error": "unauthorized"})
    op = (event.get("rawPath") or "/").strip("/")
    if op not in OPS:
        return _resp(404, {"error": "unknown operation", "operations": sorted(OPS)})
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    body = _fields(json.loads(raw))
    print({"op": op, "keys": sorted(body)})
    try:
        return _resp(200, OPS[op](body))
    except ValueError as e:
        return _resp(200, {"error": "bad_input", "message": str(e)})
