"""data.seed(reset=True) must survive seed rows that share a key with rows already in the table (DynamoDB rejects a
BatchWriteItem with two requests for the same key; the demo's `reset` command hit exactly that)."""
import importlib.util
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("kit_data", KIT / "data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeTable:
    """Mimics DynamoDB's batch rules: duplicate keys in one batch are an error unless overwrite_by_pkeys is used."""
    key_schema = [{"AttributeName": "id"}]

    def __init__(self, rows):
        self.rows = {r["id"]: dict(r) for r in rows}

    def scan(self, **_):
        return {"Items": [dict(r) for r in self.rows.values()]}

    def batch_writer(self, overwrite_by_pkeys=None):
        table = self

        class BW:
            def __init__(self):
                self.reqs = []

            def delete_item(self, Key):
                self.reqs.append(("del", Key["id"], None))

            def put_item(self, Item):
                self.reqs.append(("put", Item["id"], dict(Item)))

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                reqs = self.reqs
                if overwrite_by_pkeys:                      # later request for a key replaces the earlier one
                    last = {}
                    for r in reqs:
                        last[r[1]] = r
                    reqs = list(last.values())
                keys = [r[1] for r in reqs]
                if len(keys) != len(set(keys)):
                    raise RuntimeError("ValidationException: Provided list of item keys contains duplicates")
                for op, k, item in reqs:
                    table.rows.pop(k, None) if op == "del" else table.rows.__setitem__(k, item)
                return False
        return BW()


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    data = _load()
    (tmp_path / "seed.json").write_text('{"tables": {"T": [{"id": "a", "n": 1}, {"id": "b", "n": 2}]}}')
    cfg = {"seed": "seed.json", "region": "r"}
    fake = FakeTable([{"id": "a", "n": 99}, {"id": "zzz", "n": 5}])   # "a" overlaps the seed, "zzz" is a stray booking
    monkeypatch.setattr(data, "table", lambda c, name: fake)
    return data, tmp_path, cfg, fake


def test_reset_replaces_overlapping_rows_and_removes_strays(seeded):
    data, d, cfg, fake = seeded
    data.seed(d, cfg, reset=True)
    assert fake.rows == {"a": {"id": "a", "n": 1}, "b": {"id": "b", "n": 2}}


def test_seed_without_reset_keeps_existing_rows(seeded):
    data, d, cfg, fake = seeded
    data.seed(d, cfg, reset=False)
    assert set(fake.rows) == {"a", "zzz"}


def test_the_fake_really_rejects_duplicates():
    t = FakeTable([{"id": "a"}])
    with pytest.raises(RuntimeError):
        with t.batch_writer() as bw:
            bw.delete_item(Key={"id": "a"})
            bw.put_item(Item={"id": "a"})
