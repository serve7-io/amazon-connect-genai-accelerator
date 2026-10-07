import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

# Load by path under a unique name: lambdas/genai_gateway also has a module called handler.
_spec = importlib.util.spec_from_file_location("agent_tools_handler", Path(__file__).resolve().parents[1] / "handler.py")
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)

DB = {
    ("PHONE#+14035550101", "ALIAS"): {"customer_id": "C1001"},
    ("EMAIL#ava@example.com", "ALIAS"): {"customer_id": "C1001"},
    ("CUST#C1001", "PROFILE"): {"first_name": "Ava", "tier": "gold", "email": "ava@example.com", "phone": "+14035550101"},
}
ROWS = [
    {"pk": "CUST#C1001", "sk": "PROFILE"},
    {"pk": "CUST#C1001", "sk": "ORDER#5001", "item": "Router", "status": "shipped", "eta": "2026-10-09"},
    {"pk": "CUST#C1001", "sk": "ORDER#5002", "item": "Cable", "status": "processing", "eta": "2026-10-12"},
    {"pk": "CUST#C1001", "sk": "TICKET#T-1", "category": "billing", "status": "open"},
    {"pk": "CUST#C1001", "sk": "TICKET#T-0", "category": "billing", "status": "closed"},
]


@pytest.fixture
def table(monkeypatch):
    t = mock.Mock()
    t.get_item.side_effect = lambda Key: {"Item": DB.get((Key["pk"], Key["sk"]))} if (Key["pk"], Key["sk"]) in DB else {}
    t.query.return_value = {"Items": ROWS}
    monkeypatch.setattr(handler, "_tbl", lambda: t)
    return t


def ctx(tool):
    return SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": f"crm___{tool}"}))


def test_lookup_by_phone_normalises_and_masks_email(table):
    r = handler.lambda_handler({"phone": "(403) 555-0101"}, ctx("customer_lookup"))
    assert r == {"found": True, "customer_id": "C1001", "first_name": "Ava", "tier": "gold", "email_hint": "a***@example.com"}


def test_lookup_by_email_case_insensitive(table):
    assert handler.lambda_handler({"email": "Ava@Example.com "}, ctx("customer_lookup"))["customer_id"] == "C1001"


def test_lookup_not_found(table):
    assert handler.lambda_handler({"phone": "4035559999"}, ctx("customer_lookup"))["found"] is False


def test_status_lists_orders_and_only_open_tickets(table):
    r = handler.lambda_handler({"customer_id": "C1001"}, ctx("retrieve_status"))
    assert [o["order_id"] for o in r["orders"]] == ["5001", "5002"]
    assert r["open_tickets"] == [{"ticket_id": "T-1", "category": "billing", "status": "open"}]


def test_status_single_order_and_missing_order(table):
    assert len(handler.lambda_handler({"customer_id": "C1001", "order_id": "5001"}, ctx("retrieve_status"))["orders"]) == 1
    assert handler.lambda_handler({"customer_id": "C1001", "order_id": "999"}, ctx("retrieve_status"))["found"] is False


def test_create_ticket_writes_item(table):
    r = handler.lambda_handler({"customer_id": "C1001", "category": "Billing", "summary": "Charged twice for October"},
                               ctx("create_support_ticket"))
    assert r["created"] and r["ticket_id"].startswith("T-") and r["category"] == "billing"
    item = table.put_item.call_args.kwargs["Item"]
    assert item["pk"] == "CUST#C1001" and item["source"] == "ai-agent" and item["status"] == "open"


def test_create_ticket_rejects_unknown_customer_and_empty_summary(table):
    assert handler.lambda_handler({"customer_id": "NOPE", "summary": "help me"}, ctx("create_support_ticket"))["created"] is False
    assert handler.lambda_handler({"customer_id": "C1001", "summary": "x"}, ctx("create_support_ticket"))["created"] is False
    table.put_item.assert_not_called()


def test_unknown_category_becomes_other(table):
    r = handler.lambda_handler({"customer_id": "C1001", "category": "rocket", "summary": "Something odd happened"},
                               ctx("create_support_ticket"))
    assert r["category"] == "other"


def test_unknown_tool_and_backend_failure_are_safe(table):
    assert "unknown tool" in handler.lambda_handler({}, ctx("drop_tables"))["error"]
    table.get_item.side_effect = RuntimeError("boom")
    r = handler.lambda_handler({"customer_id": "C1001"}, ctx("retrieve_status"))
    assert "unavailable" in r["error"] and "boom" not in r["error"]


def test_direct_invoke_tool_field(table):
    assert handler.lambda_handler({"__tool": "customer_lookup", "customer_id": "C1001"})["found"] is True
