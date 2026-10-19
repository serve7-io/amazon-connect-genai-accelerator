# Renault car rental chat agent (demo)

ACXD chat assistant for **Renault Rent D.O.O.**, Belgrade (company name invented for the demo). It answers in Serbian (Latin script) or English, with prices in EUR.
**All data is invented** (fleet units, specs, prices, availability, customers).

It can:
- answer fleet questions: model, body type, fuel, manual or automatic, seats, luggage space, price per day, deposit;
- check availability for dates;
- give quotes (3–6 days −10 %, 7+ days −15 %, 250 km/day);
- book with a licence check (category B required) and a confirmation number `REN-xxxxx`;
- look up and cancel bookings (confirmation + phone).

Fleet: 10 models / 17 units:
- Twingo ×2, Clio ×3, Captur ×2, Mégane ×2, Mégane E-Tech ×2, Arkana, Austral, Scénic ×2, Kangoo, Trafic (9 seats, still category B).

Seeded so the demo has "no" answers:
- the Austral (one unit) is rented today to +6 days;
- one Scénic is in maintenance and the other is rented +1 to +5 days, so no Scénic for the coming days;
- one Mégane E-Tech is rented +2 to +4 days.

Stack: `demo-renault-rental`. Copied from `demos/yamaha-moto` and adapted: the licence rule is now "category B for every car" (a motorcycle category such as A2 is refused with `licence_too_low`).

## Operate (issue titles)
- `demo: deploy renault-rental`
- `demo: status renault-rental`
- `demo: reset renault-rental`
- `demo: test renault-rental`
- `demo: reservations renault-rental`
- `demo: set-price renault-rental <model_id> <eur>`
- `demo: maintenance renault-rental <unit_id> on|off`
- `demo: cancel renault-rental <REN-xxxxx>`
- `demo: delete renault-rental` (body: `confirm delete renault-rental`)

Model ids:
- twingo
- clio
- captur
- megane
- meganeetech
- arkana
- austral
- scenic
- kangoo
- trafic

## ACXD (manual, once)
The data requests are prefixed `rn` (`rnlistcars`, `rncheckavailability`, `rnquote`, `rncreatereservation`, `rngetreservation`, `rncancelreservation`) because the workspace already holds the Yamaha demo's `listbikes`, `quote` and so on. Import them with **Import as new** (never Replace).

1. Data requests: use the copies written by `deploy` (they carry this stack's API URL), or `acxd/datarequests/` with `YOUR-API-URL` replaced. They use the existing ACXD secret `ccg_s5_api_key`.
2. Flow: `acxd/renault-car-rental.flow.json` (Settings > Advanced > Import > Import as new flow).
3. Application: `acxd/renault-car-rental.application.json`, or New application > Add flow (Welcome) > Save.
4. Build & deploy stays a human click.

## Demo script (chat)
1. "Hi, I need a car for 7 people next week" The agent suggests the Trafic.
2. "Is the Scenic free this weekend?" The agent says no and offers alternatives.
3. "Koliko košta Clio od petka do ponedeljka?" (Serbian) The agent quotes with the 10 % discount.
4. "Rezerviši, Milan Ilić, 064 123 4567, B kategorija" The agent shows a summary, the customer says yes, and gets REN-xxxxx.
5. "Can you cancel REN-xxxxx? My phone is 064 123 4567"
6. Licence check: "I only have an A2 licence, can I rent a Clio?" The agent explains category B is required.

## Web test page
This demo has a CloudFront test page with the Touchpoint chat (see `demos/_kit/README.md`, "Web test page"). The `web` block is in `demo.json`.
- One-time: `bash demos/_kit/web/set-acxd-keys.sh renault-rental` in CloudShell (the app's Production deployment key, optional Live Sync API key).
- Publish: `demo: deploy renault-rental` (creates the chat flow and the page) or `demo: web renault-rental` (page only). The page URL is in the result comment.
- Renault car rental: the page is `https://<cloudfront domain>/renault-rental/`.
