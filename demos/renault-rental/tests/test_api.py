"""Offline tests for the Renault rental API (fake DynamoDB tables, fixed 'today')."""
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
    data = json.loads((ROOT / "data" / "seed.json").read_text(encoding="utf-8"))

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


def test_seed_shape():
    fleet, res = _seed()
    assert len(fleet) == 17 and len({r["model_id"] for r in fleet}) == 10 and len(res) == 3
    assert {r["licence"] for r in fleet} == {"B"}
    assert len({r["unit_id"] for r in fleet}) == 17                    # unique keys


def test_auth_and_health(api):
    assert call(api, "list_cars", {}, key="bad")[0] == 401
    code, body = call(api, "health", {}, key="")
    assert code == 200 and body["units"] == 17 and body["key_configured"]
    code, body = call(api, "nope", {})
    assert code == 404 and "list_cars" in body["operations"]


def test_list_cars_filters(api):
    _, b = call(api, "list_cars", {})
    assert b["count"] == 10 and b["currency"] == "EUR" and b["today"] == TODAY.isoformat()
    assert b["cars"][0]["model"] == "Twingo"                           # cheapest first
    assert b["cars"][-1]["model"] == "Trafic"
    _, lic_b = call(api, "list_cars", {"licence": "B"})
    assert lic_b["count"] == 10
    _, lic_a2 = call(api, "list_cars", {"licence": "A2"})              # motorcycle licence: no cars
    assert lic_a2["count"] == 0
    _, family = call(api, "list_cars", {"min_seats": 5, "max_price_per_day": 45})
    assert {m["model"] for m in family["cars"]} == {"Clio", "Captur", "M\u00e9gane"}
    _, group = call(api, "list_cars", {"min_seats": 7})
    assert [m["model"] for m in group["cars"]] == ["Trafic"] and group["cars"][0]["seats"] == 9
    _, ev = call(api, "list_cars", {"fuel": "electric"})
    assert [m["model"] for m in ev["cars"]] == ["M\u00e9gane E-Tech"]
    _, auto = call(api, "list_cars", {"transmission": "automatic", "type": "suv"})
    assert {m["model"] for m in auto["cars"]} == {"Captur", "Arkana", "Austral"}
    _, one = call(api, "list_cars", {"model": "renault clio"})
    assert [m["model"] for m in one["cars"]] == ["Clio"] and one["cars"][0]["units_in_fleet"] == 3
    _, accent = call(api, "list_cars", {"model": "Scenic"})            # accents are optional
    assert [m["model"] for m in accent["cars"]] == ["Sc\u00e9nic"] and accent["cars"][0]["units_in_fleet"] == 1


def test_model_matching(api):
    _, e = call(api, "list_cars", {"model": "megane e-tech"})
    assert [m["model"] for m in e["cars"]] == ["M\u00e9gane E-Tech"]
    _, m = call(api, "list_cars", {"model": "M\u00e9gane"})              # exact match wins over E-Tech
    assert [x["model"] for x in m["cars"]] == ["M\u00e9gane"]


def test_availability(api):
    _, a = call(api, "check_availability", {"start_date": d(1), "end_date": d(3)})
    free = {m["model"]: m["free_units"] for m in a["available"]}
    assert "Austral" not in free and "Sc\u00e9nic" not in free         # rented / other unit in maintenance
    assert set(a["not_available"]) == {"Austral", "Sc\u00e9nic"}
    assert free["M\u00e9gane E-Tech"] == 1 and free["Clio"] == 3 and a["days"] == 2
    _, later = call(api, "check_availability", {"start_date": d(10), "end_date": d(12), "model": "austral"})
    assert later["available"][0]["model"] == "Austral"
    _, past = call(api, "check_availability", {"start_date": d(-1)})
    assert past["error"] == "bad_input"
    _, nomodel = call(api, "check_availability", {"start_date": d(1), "model": "Ferrari"})
    assert nomodel["found"] is False


def test_quote_discounts(api):
    _, q1 = call(api, "quote", {"model": "Clio", "start_date": d(1), "end_date": d(2)})
    assert q1["total_eur"] == 32 and q1["discount_percent"] == 0 and q1["deposit_eur"] == 300
    _, q3 = call(api, "quote", {"model": "Clio", "start_date": d(1), "end_date": d(4)})
    assert q3["subtotal_eur"] == 96 and q3["discount_percent"] == 10 and q3["total_eur"] == 86.4
    _, q7 = call(api, "quote", {"model": "Trafic", "start_date": d(10), "end_date": d(17)})
    assert q7["discount_percent"] == 15 and q7["total_eur"] == 505.75 and q7["km_included"] == 1750
    _, same = call(api, "quote", {"model": "Twingo", "start_date": d(1)})
    assert same["days"] == 1 and same["total_eur"] == 25
    _, unknown = call(api, "quote", {"model": "Ferrari", "start_date": d(1)})
    assert unknown["found"] is False


def test_reservation_flow(api):
    base = {"model": "M\u00e9gane E-Tech", "start_date": d(2), "end_date": d(4), "customer_name": "Milan Ilic",
            "phone": "+381 64 123 4567", "licence": "B"}
    _, r1 = call(api, "create_reservation", base)
    assert r1["created"] and r1["unit_id"] == "RN-MEGANEETECH-02" and r1["confirmation"].startswith("REN-")
    assert r1["total_eur"] == 120
    _, r2 = call(api, "create_reservation", base)                       # both E-Tech units now taken
    assert r2 == {"created": False, "reason": "not_available", "model": "M\u00e9gane E-Tech"}
    _, g = call(api, "get_reservation", {"confirmation": r1["confirmation"].lower()})
    assert g["found"] and g["model"] == "M\u00e9gane E-Tech" and g["customer_first_name"] == "Milan"
    _, bad = call(api, "cancel_reservation", {"confirmation": r1["confirmation"], "phone": "+381 60 000 0000"})
    assert bad == {"cancelled": False, "reason": "not_found"}
    _, ok = call(api, "cancel_reservation", {"confirmation": r1["confirmation"], "phone": "064 123 4567"})
    assert ok["cancelled"]
    _, again = call(api, "create_reservation", base)                    # unit free again
    assert again["created"]


def test_reservation_rules(api):
    base = {"start_date": d(1), "end_date": d(2), "customer_name": "Ana", "phone": "0601234567"}
    _, low = call(api, "create_reservation", dict(base, model="Clio", licence="A2"))
    assert low["reason"] == "licence_too_low" and low["required_licence"] == "B"
    _, unk = call(api, "create_reservation", dict(base, model="Clio", licence="X9"))
    assert unk["reason"] == "unknown_licence"
    _, ok = call(api, "create_reservation", dict(base, model="Captur", licence="b"))
    assert ok["created"] and ok["total_eur"] == 40
    _, miss = call(api, "create_reservation", {"model": "Clio", "start_date": d(1)})
    assert miss == {"created": False, "missing": "customer_name"}
    _, busy = call(api, "create_reservation", dict(base, model="Austral", licence="B"))
    assert busy["reason"] == "not_available"
    _, maint = call(api, "create_reservation", dict(base, model="Sc\u00e9nic", licence="B"))
    assert maint["reason"] == "not_available"                           # the free unit is in maintenance / other rented


def test_wrapped_payload(api):
    _, q = call(api, "quote", {"payload": {"model": "Twingo", "start_date": d(1), "end_date": d(2)}, "nlx_context": {"x": 1}})
    assert q["model"] == "Twingo" and q["total_eur"] == 25
