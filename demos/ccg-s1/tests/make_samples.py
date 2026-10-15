"""Regenerate samples/<op>.json (success and error shapes) from the real handler and the seed data, so the ACXD
response models can be generated from them. Run: python3 tests/make_samples.py"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas" / "api"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["API_KEY"] = "k"
import handler  # noqa: E402
from test_api import FakeDb  # noqa: E402

db = FakeDb(handler.SEED)
db.h = handler
handler.DB = db
S = "M-20481537"
CASES = {
    "lookupmember": ({"member_id": S}, {"member_id": "M-99999999"}),
    "verifyservice": ({"member_id": S, "vehicle_confirmed": "2023 Honda CR-V", "interaction_type": "service"},
                      {"member_id": S, "vehicle_confirmed": "Ford F-150", "interaction_type": "service"}),
    "coverage": ({"member_id": S, "service": "tow"}, {"member_id": S, "service": "helicopter"}),
    "dispatch": ({"member_id": S, "location": "Highway 401 eastbound near Joyceville Road, Kingston", "service": "tow",
                  "destination": "nearest_certified"}, {"member_id": S, "service": "tow"}),
    "writeback": ({"contact_id": "0f8fad5b-d9cb-469f-a165-70867728950e", "member_id": S,
                   "disposition": "Roadside, Tow Dispatched", "dispatch_id": "D-20261018-4821",
                   "summary": "Flat tire, no spare, Hwy 401 EB near Joyceville Rd. Safe off the road. Tow to Princess St."},
                  {"member_id": S}),
    "reset": ({"member_id": S}, {"member_id": "M-99999999"}),
}
out_dir = ROOT / "samples"
out_dir.mkdir(exist_ok=True)
for op, (ok_req, err_req) in CASES.items():
    sample = {"operation": op, "path": f"/{op}",
              "success": {"request": ok_req, "response": handler._clean(handler.OPS[op](dict(ok_req)))},
              "error": {"request": err_req, "response": handler._clean(handler.OPS[op](dict(err_req)))}}
    (out_dir / f"{op}.json").write_text(json.dumps(sample, indent=2, ensure_ascii=False) + "\n")
    print(op, "ok")
