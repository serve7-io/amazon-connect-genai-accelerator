"""Offline tests for the Yamaha rental API (fake DynamoDB tables, fixed 'today')."""
import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 10, 14)


class FakeTable:
    def __init__(self, key, rows=()):
        self.key, self.rows = key, {r[key]: dict(r) for r in rows}

    def scan(self, **_):
        return {"Items": [dict(r) for r in self.rows.values()]}

    def get_item(self, Key):
        r = self.rows.get(Key[self.key])
        return {"Item": dict(r)} if r else {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression and Item[self.key] in self.rows:
            raise type("ConditionalCheckFailedException", (Exception,), {})()
        self.rows[Item[self.key]] = dict(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ExpressionAttributeNames=None, **_):
        self.rows[Key[self.key]]["status"] = ExpressionAttributeValues[":c"]


def _seed():
    data = json.loads((ROOT / "data" / "seed.json").read_text())
    def fix(r):
        out = {}
        for k, v in r.items():
            if isinstance(v, str) and (k.endswith("_date") or k == "end_exclusive") and v[:1] == "+":
                v = (TODAY + dt.timedelta(days=int(v))).isoformat()
            out[k] = Decimal(str(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
        return out
    return [fix(r) for r in data["tables"]["FleetTable"]], [fix(r) for r in data["tables"]["ReservationsTable"]]


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("TODAY", TODAY.isoformat())
    sys.path.insert(0, str(ROOT / "lambdas" / "api")); sys.modules.pop("handler", None)
    import handler
    fleet, res = _seed()
    handler.FLEET, handler.RES = FakeTable("unit_id", fleet), FakeTable("confirmation", res)
    yield handler
    sys.path.remove(str(ROOT / "lambdas" / "api"))


def call(h, op, body, key="k"):
    r = h.handler({"rawPath": f"/{op}", "headers": {"x-api-key": key}, "body": json.dumps(body)}, None)
    return r["statusCode"], json.loads(r["body"])


def d(n):
    return (TODAY + dt.timedelta(days=n)).isoformat()


def test_auth_and_health(api):
    assert call(api, "list_bikes", {}, key="bad")[0] == 401
    code, body = call(api, "health", {}, key="")
    assert code == 200 and body["units"] == 17 and body["key_configured"]


def test_list_bikes_filters(api):
    _, b = call(api, "list_bikes", {})
    assert b["count"] == 10 and b["currency"] == "EUR" and b["today"] == TODAY.isoformat()
    assert b["bikes"][0]["model"] == "NMAX 125"                        # cheapest first
    _, a2 = call(api, "list_bikes", {"licence": "A2"})
    assert {m["model"] for m in a2["bikes"]} == {"MT-125", "NMAX 125", "XMAX 300", "TMAX 560"}
    _, big = call(api, "list_bikes", {"min_cc": 600, "max_price_per_day": 70})
    assert {m["model"] for m in big["bikes"]} == {"MT-07", "XSR700"}
    _, one = call(api, "list_bikes", {"model": "yamaha mt 07"})
    assert [m["model"] for m in one["bikes"]] == ["MT-07"] and one["bikes"][0]["cc"] == 689


def test_availability(api):
    _, a = call(api, "check_availability", {"start_date": d(1), "end_date": d(3)})
    free = {m["model"]: m["free_units"] for m in a["available"]}
    assert "Tenere 700" not in free and "Tracer 9 GT" not in free      # rented / other unit in maintenance
    assert set(a["not_available"]) == {"Tenere 700", "Tracer 9 GT"}
    assert free["MT-09"] == 1 and free["MT-07"] == 3 and a["days"] == 2
    _, later = call(api, "check_availability", {"start_date": d(10), "end_date": d(12), "model": "tenere"})
    assert later["available"][0]["model"] == "Tenere 700"
    _, past = call(api, "check_availability", {"start_date": d(-1)})
    assert past["error"] == "bad_input"


def test_quote_discounts(api):
    _, q1 = call(api, "quote", {"model": "MT-07", "start_date": d(1), "end_date": d(2)})
    assert q1["total_eur"] == 65 and q1["discount_percent"] == 0 and q1["deposit_eur"] == 700
    _, q3 = call(api, "quote", {"model": "MT-07", "start_date": d(1), "end_date": d(4)})
    assert q3["subtotal_eur"] == 195 and q3["discount_percent"] == 10 and q3["total_eur"] == 175.5
    _, q7 = call(api, "quote", {"model": "Tracer 9 GT", "start_date": d(10), "end_date": d(17)})
    assert q7["discount_percent"] == 15 and q7["total_eur"] == 565.25 and q7["km_included"] == 1750
    _, same = call(api, "quote", {"model": "NMAX 125", "start_date": d(1)})
    assert same["days"] == 1 and same["total_eur"] == 30


def test_reservation_flow(api):
    base = {"model": "MT-09", "start_date": d(2), "end_date": d(4), "customer_name": "Milan Ilic",
            "phone": "+381 64 123 4567", "licence": "A"}
    _, r1 = call(api, "create_reservation", base)
    assert r1["created"] and r1["unit_id"] == "YM-MT09-02" and r1["confirmation"].startswith("YAM-")
    _, r2 = call(api, "create_reservation", base)                       # both MT-09 now taken
    assert r2 == {"created": False, "reason": "not_available", "model": "MT-09"}
    _, g = call(api, "get_reservation", {"confirmation": r1["confirmation"].lower()})
    assert g["found"] and g["model"] == "MT-09" and g["customer_first_name"] == "Milan"
    _, bad = call(api, "cancel_reservation", {"confirmation": r1["confirmation"], "phone": "+381 60 000 0000"})
    assert bad == {"cancelled": False, "reason": "not_found"}
    _, ok = call(api, "cancel_reservation", {"confirmation": r1["confirmation"], "phone": "064 123 4567"})
    assert ok["cancelled"]
    _, again = call(api, "create_reservation", base)                    # unit free again
    assert again["created"]


def test_reservation_rules(api):
    base = {"start_date": d(1), "end_date": d(2), "customer_name": "Ana", "phone": "0601234567"}
    _, low = call(api, "create_reservation", dict(base, model="MT-07", licence="A2"))
    assert low["reason"] == "licence_too_low" and low["required_licence"] == "A"
    _, ok = call(api, "create_reservation", dict(base, model="XMAX 300", licence="A2"))
    assert ok["created"]
    _, miss = call(api, "create_reservation", {"model": "R7", "start_date": d(1)})
    assert miss == {"created": False, "missing": "customer_name"}
    _, busy = call(api, "create_reservation", dict(base, model="Tenere 700", licence="A"))
    assert busy["reason"] == "not_available"


def test_wrapped_payload(api):
    _, q = call(api, "quote", {"payload": {"model": "R7", "start_date": d(1), "end_date": d(2)}, "nlx_context": {"x": 1}})
    assert q["model"] == "R7" and q["total_eur"] == 75
