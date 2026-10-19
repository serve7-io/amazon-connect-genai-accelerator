# Yamaha moto rental chat agent (demo)

ACXD chat assistant for **Yamaha Motorcycle D.O.O.**, Belgrade. It answers in Serbian (Latin script) or English, with prices in EUR.
**All data is invented** (fleet units, prices, availability, customers).

It can:
- answer fleet questions: model, cc, kW, licence category, price per day, deposit, seat height;
- check availability for dates;
- give quotes (3–6 days −10 %, 7+ days −15 %, 250 km/day);
- book with a licence check and a confirmation number `YAM-xxxxx`;
- look up and cancel bookings (confirmation + phone).

Fleet: 10 models / 17 units:
- MT-125, NMAX 125, XMAX 300, TMAX 560, MT-07 ×3, XSR700, R7, Ténéré 700, MT-09 ×2, Tracer 9 GT ×2.

Seeded so the demo has "no" answers:
- the Ténéré 700 is rented today to +6 days;
- one Tracer 9 GT is in maintenance and the other is rented +1 to +5 days, so no Tracer for the coming days;
- one MT-09 is rented +2 to +4 days.

Operate (issue titles):
- `demo: deploy yamaha-moto`
- `demo: status yamaha-moto`
- `demo: reset yamaha-moto`
- `demo: reservations yamaha-moto`
- `demo: set-price yamaha-moto <model_id> <eur>`
- `demo: maintenance yamaha-moto <unit_id> on|off`
- `demo: cancel yamaha-moto <YAM-xxxxx>`
- `demo: delete yamaha-moto` (body: `confirm delete yamaha-moto`)

Model ids:
- mt125
- nmax125
- xmax300
- tmax560
- mt07
- xsr700
- r7
- tenere700
- mt09
- tracer9gt

## Demo script (chat)
1. "Hi, what bikes can I rent with an A2 licence?"
2. "Is the Tracer 9 GT free this weekend?" The agent says no and offers alternatives.
3. "Koliko košta MT-07 od petka do ponedeljka?" (Serbian) The agent quotes with the 10 % discount.
4. "Rezerviši, Milan Ilić, 064 123 4567, A kategorija" The agent shows a summary, the customer says yes, and gets YAM-xxxxx.
5. "Can you cancel YAM-xxxxx? My phone is 064 123 4567"

## Web test page
This demo has a CloudFront test page with the Touchpoint chat (see `demos/_kit/README.md`, "Web test page"). The `web` block is in `demo.json`.
- One-time: `bash demos/_kit/web/set-acxd-keys.sh yamaha-moto` in CloudShell (the app's Production deployment key, optional Live Sync API key).
- Publish: `demo: deploy yamaha-moto` (creates the chat flow and the page) or `demo: web yamaha-moto` (page only). The page URL is in the result comment.
- Yamaha motorcycle rental: the page is `https://<cloudfront domain>/yamaha-moto/`.
