"""Renault rental demo admin commands (python3 demos/_kit/data.py renault-rental <command> ...).

  set-price <model_id> <eur>        e.g. set-price clio 35       (all units of that model)
  maintenance <unit_id> on|off      e.g. maintenance RN-CLIO-02 on
  reservations                      list confirmed reservations
  cancel <confirmation>             e.g. cancel REN-10021
"""
from decimal import Decimal

MODEL_IDS = "twingo clio captur megane meganeetech arkana austral scenic kangoo trafic"


def set_price(table, scan, model_id, eur):
    t = table("FleetTable")
    rows = [r for r in scan(t) if r["model_id"] == model_id.lower()]
    if not rows:
        raise SystemExit(f"no model_id '{model_id}' (ids: {MODEL_IDS})")
    for r in rows:
        t.update_item(Key={"unit_id": r["unit_id"]}, UpdateExpression="SET price_per_day = :p",
                      ExpressionAttributeValues={":p": Decimal(eur)})
    print(f"{rows[0]['model']}: price per day {rows[0]['price_per_day']} -> {eur} EUR ({len(rows)} units)")


def maintenance(table, scan, unit_id, state):
    on = state.lower() in ("on", "yes", "true", "1")
    table("FleetTable").update_item(Key={"unit_id": unit_id.upper()}, UpdateExpression="SET maintenance = :m",
                                    ConditionExpression="attribute_exists(unit_id)",
                                    ExpressionAttributeValues={":m": on})
    print(f"{unit_id.upper()}: maintenance {'ON (not rentable)' if on else 'OFF (rentable)'}")


def reservations(table, scan):
    rows = sorted((r for r in scan(table("ReservationsTable")) if r["status"] == "confirmed"),
                  key=lambda r: r["start_date"])
    for r in rows:
        print(f"{r['confirmation']}  {r['model']:<16} {r['unit_id']:<20} {r['start_date']} -> {r['end_exclusive']}"
              f"  {r['days']}d  {r['total_eur']} EUR  {r['customer_name']}  ({r.get('channel', '')})")
    print(f"{len(rows)} confirmed")


def cancel(table, scan, confirmation):
    table("ReservationsTable").update_item(Key={"confirmation": confirmation.upper()}, UpdateExpression="SET #s = :c",
                                           ConditionExpression="attribute_exists(confirmation)",
                                           ExpressionAttributeNames={"#s": "status"},
                                           ExpressionAttributeValues={":c": "cancelled"})
    print(f"{confirmation.upper()}: cancelled")


COMMANDS = {"set-price": set_price, "maintenance": maintenance, "reservations": reservations, "cancel": cancel}
