"""Serve7 agent tools - MCP tools behind an Amazon Bedrock AgentCore Gateway (Lambda target).

Tools (sandbox backs them with a demo CRM table of fictional customers):
  customer_lookup        read   find a customer by phone, email or customer id
  retrieve_status        read   status of a customer's orders / open tickets
  create_support_ticket  write  open a ticket (the AI agent asks the customer to confirm first)

AgentCore passes the tool input as the event and the tool name in
context.client_context.custom["bedrockAgentCoreToolName"] as "<target>___<tool>".

Data model (single table, pk/sk):
  CUST#<id>     PROFILE        first_name, last_name, tier, phone, email
  CUST#<id>     ORDER#<id>     item, status, eta
  CUST#<id>     TICKET#<id>    category, summary, status, created_at
  PHONE#<e164>  ALIAS          customer_id
  EMAIL#<lower> ALIAS          customer_id
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

log = logging.getLogger()
log.setLevel(logging.INFO)

TABLE = os.environ.get("CRM_TABLE", "")
TICKET_CATEGORIES = {"billing", "technical", "order", "account", "other"}
_table = None


def _tbl():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(TABLE)
    return _table


# ------------------------------------------------------------------ helpers
def _norm_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "1" + digits
    return f"+{digits}" if digits else ""


def _mask_email(email: str) -> str:
    user, _, domain = (email or "").partition("@")
    return f"{user[:1]}***@{domain}" if domain else ""


def _resolve_customer_id(args: dict) -> str | None:
    if args.get("customer_id"):
        return str(args["customer_id"]).strip()
    key = None
    if args.get("phone"):
        key = f"PHONE#{_norm_phone(args['phone'])}"
    elif args.get("email"):
        key = f"EMAIL#{str(args['email']).strip().lower()}"
    if not key:
        return None
    item = _tbl().get_item(Key={"pk": key, "sk": "ALIAS"}).get("Item")
    return item["customer_id"] if item else None


def _profile(cid: str) -> dict | None:
    return _tbl().get_item(Key={"pk": f"CUST#{cid}", "sk": "PROFILE"}).get("Item")


# ------------------------------------------------------------------ tools
def customer_lookup(args: dict) -> dict:
    cid = _resolve_customer_id(args)
    prof = _profile(cid) if cid else None
    if not prof:
        return {"found": False, "message": "No customer matches those details."}
    return {
        "found": True,
        "customer_id": cid,
        "first_name": prof.get("first_name"),
        "tier": prof.get("tier"),
        "email_hint": _mask_email(prof.get("email", "")),  # never return full PII to the model
    }


def retrieve_status(args: dict) -> dict:
    cid = _resolve_customer_id(args)
    if not cid or not _profile(cid):
        return {"found": False, "message": "Customer not found; look the customer up first."}
    items = _tbl().query(KeyConditionExpression=Key("pk").eq(f"CUST#{cid}"))["Items"]
    order_id = str(args.get("order_id") or "").strip()
    orders = [
        {"order_id": i["sk"].split("#", 1)[1], "item": i.get("item"), "status": i.get("status"), "eta": i.get("eta")}
        for i in items if i["sk"].startswith("ORDER#") and (not order_id or i["sk"] == f"ORDER#{order_id}")
    ]
    tickets = [
        {"ticket_id": i["sk"].split("#", 1)[1], "category": i.get("category"), "status": i.get("status")}
        for i in items if i["sk"].startswith("TICKET#") and i.get("status") != "closed"
    ]
    if order_id and not orders:
        return {"found": False, "message": f"No order {order_id} on this account."}
    return {"found": True, "customer_id": cid, "orders": orders, "open_tickets": tickets}


def create_support_ticket(args: dict) -> dict:
    cid = _resolve_customer_id(args)
    if not cid or not _profile(cid):
        return {"created": False, "message": "Customer not found; look the customer up first."}
    category = str(args.get("category") or "other").lower()
    if category not in TICKET_CATEGORIES:
        category = "other"
    summary = str(args.get("summary") or "").strip()[:500]
    if len(summary) < 5:
        return {"created": False, "message": "A short summary of the issue is required."}
    ticket_id = "T-" + uuid.uuid4().hex[:8].upper()
    _tbl().put_item(Item={
        "pk": f"CUST#{cid}", "sk": f"TICKET#{ticket_id}", "category": category, "summary": summary,
        "status": "open", "created_at": int(time.time()), "source": "ai-agent",
    })
    return {"created": True, "ticket_id": ticket_id, "category": category, "status": "open"}


TOOLS = {
    "customer_lookup": customer_lookup,
    "retrieve_status": retrieve_status,
    "create_support_ticket": create_support_ticket,
}


def _tool_name(event: dict, context: Any) -> str:
    name = ""
    try:
        name = context.client_context.custom.get("bedrockAgentCoreToolName", "")
    except AttributeError:
        pass
    name = name or event.pop("__tool", "")  # local tests / direct invoke
    return name.split("___")[-1]


def lambda_handler(event: dict, context: Any = None) -> dict:
    event = dict(event or {})
    tool = _tool_name(event, context)
    started = time.monotonic()
    fn = TOOLS.get(tool)
    if not fn:
        result = {"error": f"unknown tool '{tool}'"}
    else:
        try:
            result = fn(event)
        except Exception:  # never leak stack traces or data to the model
            log.exception("tool failed")
            result = {"error": "The system is unavailable right now. Offer to connect the customer with an agent."}
    # Log tool, outcome and latency only - no customer data.
    log.info(json.dumps({"tool": tool, "ok": "error" not in result,
                         "ms": int((time.monotonic() - started) * 1000)}))
    return result
