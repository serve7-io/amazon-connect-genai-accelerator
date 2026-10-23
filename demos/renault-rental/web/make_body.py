#!/usr/bin/env python3
"""Generate web/body.html (fleet cards, booking form, quote and confirmation panels) from data/seed.json.

    python3 demos/renault-rental/web/make_body.py          # rewrite web/body.html
    python3 demos/renault-rental/web/make_body.py --check  # exit 1 if web/body.html is out of date (used by the tests)

The booking form uses real <label>s: Touchpoint's automatic Live Sync context reads them, so the assistant can fill
the fields by name. Everything on the page is synthetic demo data.
"""
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = HERE.parent / "data" / "seed.json"
LICENCES = [("B", "B (car)"), ("A2", "A2 (motorcycle)"), ("A1", "A1 (motorcycle)"), ("A", "A (motorcycle)"), ("AM", "AM (moped)")]


def models():
    rows = json.loads(SEED.read_text(encoding="utf-8"))["tables"]["FleetTable"]
    seen = {}
    for r in rows:
        seen.setdefault(r["model_id"], r)
    return sorted(seen.values(), key=lambda r: (r["price_per_day"], r["model"]))


def build():
    e = html.escape
    ms = models()
    cards = "\n".join(
        f'''      <article class="car" id="car-{e(m["model_id"])}" data-model-id="{e(m["model_id"])}">
        <h3>{e(m["model"])}</h3>
        <p class="meta">{e(m["type"])} &middot; {e(m["fuel"])} &middot; {e(m["transmission"])}</p>
        <p class="meta">{m["seats"]} seats &middot; {m["luggage_l"]} l luggage</p>
        <p class="price"><b>{m["price_per_day"]} EUR</b> / day <span>deposit {m["deposit"]} EUR</span></p>
        <p class="note">{e(m["highlights"])}</p>
      </article>'''
        for m in ms)
    model_opts = "\n".join(f'          <option value="{e(m["model_id"])}">{e(m["model"])}</option>' for m in ms)
    lic_opts = "\n".join(f'          <option value="{e(v)}">{e(t)}</option>' for v, t in LICENCES)
    return f'''  <section class="card" id="fleet-card">
    <h2>Our cars</h2>
    <p class="hint">Ask the assistant what suits you; the matching cars light up here.</p>
    <div class="fleet" id="fleet">
{cards}
    </div>
  </section>

  <section class="card" id="booking-card">
    <h2>Your booking</h2>
    <p class="hint">The assistant fills this in while you chat. Tell it when you are ready to book. No payment is taken online.</p>
    <div class="booking" id="booking">
      <div class="field">
        <label for="model">Car model</label>
        <select id="model" name="model">
          <option value="">Choose a car</option>
{model_opts}
        </select>
      </div>
      <div class="field">
        <label for="start_date">Pickup date</label>
        <input type="date" id="start_date" name="start_date">
      </div>
      <div class="field">
        <label for="end_date">Return date</label>
        <input type="date" id="end_date" name="end_date">
      </div>
      <div class="field">
        <label for="licence">Driving licence category</label>
        <select id="licence" name="licence">
          <option value="">Choose a category</option>
{lic_opts}
        </select>
      </div>
      <div class="field">
        <label for="customer_name">Full name</label>
        <input type="text" id="customer_name" name="customer_name" autocomplete="off" placeholder="First and last name">
      </div>
      <div class="field">
        <label for="phone">Phone number</label>
        <input type="tel" id="phone" name="phone" autocomplete="off" placeholder="e.g. 064 123 4567">
      </div>
    </div>
  </section>

  <section class="card panel" id="quote-card" hidden>
    <h2>Your price</h2>
    <dl class="facts">
      <dt>Car</dt><dd id="q-model"></dd>
      <dt>Dates</dt><dd id="q-dates"></dd>
      <dt>Days</dt><dd id="q-days"></dd>
      <dt>Subtotal</dt><dd id="q-subtotal"></dd>
      <dt>Discount</dt><dd id="q-discount"></dd>
      <dt>Total</dt><dd id="q-total" class="total"></dd>
      <dt>Deposit (card hold)</dt><dd id="q-deposit"></dd>
      <dt>Kilometres included</dt><dd id="q-km"></dd>
    </dl>
  </section>

  <section class="card panel ok" id="confirm-card" hidden>
    <h2>Booking confirmed</h2>
    <p class="big" id="c-number"></p>
    <dl class="facts">
      <dt>Car</dt><dd id="c-model"></dd>
      <dt>Dates</dt><dd id="c-dates"></dd>
      <dt>Total</dt><dd id="c-total"></dd>
      <dt>Deposit (card hold)</dt><dd id="c-deposit"></dd>
    </dl>
    <p class="hint">Pickup in Belgrade, 09:00-18:00. Bring your driving licence and ID.</p>
  </section>
'''


if __name__ == "__main__":
    out = HERE / "body.html"
    text = build()
    if "--check" in sys.argv:
        sys.exit(0 if out.is_file() and out.read_text(encoding="utf-8") == text else 1)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(models())} cars)")
