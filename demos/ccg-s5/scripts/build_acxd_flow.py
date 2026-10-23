#!/usr/bin/env python3
"""Generate acxd/ccg-renewal-payment.flow.json (ACXD import format learned on 2026-10-10, see claude/acxd-livesync.md).

Deterministic ids (uuid5) so re-running gives a stable diff."""
import json, uuid
from pathlib import Path

NS = uuid.UUID("5e7e0007-cc95-4c0c-9a5e-000000000005")
nid = lambda name: str(uuid.uuid5(NS, name))
START, AGENT, END = nid("start"), nid("agent"), nid("end")

PROMPT = """You are the renewals assistant for CCG Insurance (fictional demo data only). Voice channel: one or two short sentences per turn, plain words, no lists.

Goal: help an authenticated member pay the full renewal amount of an auto or property policy on a full-payment plan.

Steps, in order:
1. Confirm the reason for the call is paying a renewal. Coverage changes, claims or monthly plans are out of scope: offer a team member (outcome escalate).
2. Authenticate: call verify_member with the caller's phone number from the context variable ani, the policy number and the date of birth the caller gives. Never say which detail did not match. After 3 failed attempts, outcome escalate with reason auth_failed.
3. Call get_policies. State the policy (last 4 digits only), renewal date, plan type and full amount due. Mention every other policy with a renewal due, and ask which one to pay first. Only state amounts a tool returned.
4. Call get_payment_methods. Offer the stored card by brand and last 4 digits only, or a new card entered on the phone keypad.
5. Read back exactly: "You authorize CCG to charge [amount] to [card] for policy ending [last 4] today. Please say yes to confirm." Only a clear yes counts; anything else, ask again once, then offer a team member. Call record_authorization.
6. Stored card: call charge_stored_card. New card: call set_outcome with collect_new_card. The phone system takes the card number on the keypad with recording paused, then returns the result to you as payment_result.
7. Approved: give the confirmation number, offer a text receipt (only if sms_capable is true, otherwise email), then offer to pay any other renewal due in the same call (repeat from step 4 with a new authorization).
8. Declined: offer one more card. A second decline or a payment system error: offer, in this order, a secure payment link by text (only if sms_capable is true), a callback once payments work again, or a team member.

Hard rules:
- Never ask for, repeat or accept a card number, expiry date or security code by voice. If the caller starts reading card digits, interrupt politely: "Please don't read your card number out loud; I'll let you enter it on your keypad."
- Never invent amounts, dates, confirmation numbers or policy details.
- Partial payments are not possible on a full-payment plan.
- If the caller asks for a person at any time, call set_outcome with escalate."""

def s(title, desc, sensitive=False):
    return {"type": "string", "isSensitive": sensitive, "title": title, "description": desc}

def action(name, prompt, inp, out):
    return {"scopeTags": ["payment"], "type": "custom", "name": name, "prompt": prompt,
            "inputSchema": inp, "outputSchema": out, "schema": out}

ACTIONS = [
    action("set_selected_policy", "Use when the member confirms which policy to pay.",
           s("policies", "Policies with a renewal due, as returned by get_policies."),
           s("policy_id", "The id of the selected policy, exactly as get_policies returned it.")),
    action("record_authorization", "Use right after the member clearly says yes to the authorization read-back.",
           s("readback", "The exact authorization sentence that was read to the member."),
           s("authorization", "The amount and card that were authorized, for example: 1284.60 CAD, Visa ending 1234.")),
    action("set_outcome", "Use to end your part of the call: collect_new_card, paid_done, sms_link, callback, escalate.",
           s("allowed", "collect_new_card = member wants to enter a new card on the keypad; paid_done = all payments finished; sms_link = send a secure payment link by text; callback = call back when payments work again; escalate = transfer to a team member."),
           s("outcome", "Exactly one of: collect_new_card, paid_done, sms_link, callback, escalate. For escalate, add the reason after a colon, for example escalate:auth_failed.")),
]

def child(target, status):
    return {"nodeId": target, "name": "", "conditions": [{"left": {"type": "node_status"}, "operator": "eq",
            "right": {"type": "constant", "value": status}}]}

canvas = lambda x, y, h: {"x": x, "y": y, "width": 240, "height": h, "isMinimized": False}
flow = {
    "flowId": "ccgrenewalpayment",
    "description": "CCG RFP Scenario 5 (synthetic data): voice renewal payment. Card data is never collected here; the Connect secure segment does that.",
    "aiDescription": "", "untrained": True, "slotTypes": [],
    "metadata": {"path": "", "tags": ["serve7-demo", "ccg-rfp", "scenario-5"]},
    "mainLanguageCode": "en-US", "languageCodes": ["en-US"], "mirroring": "none",
    "nodes": {
        START: {"nodeId": START, "childNodes": [{"nodeId": AGENT, "name": "", "conditions": []}], "type": "start",
                "messages": [], "modalities": {}, "metadata": {"stateModifications": [], "name": ""},
                "canvasMetadata": canvas(-1380, -360, 199)},
        AGENT: {"nodeId": AGENT, "childNodes": [child(END, x) for x in ("continuation", "timeout", "failure", "escalation")],
                "type": "multimodal", "messages": [], "modalities": {},
                "metadata": {"stateModifications": [], "name": "Renewal payment agent",
                             "multimodal": {"type": "agentic", "prompt": PROMPT, "actions": ACTIONS, "exitConditions": [],
                                            "tools": [], "maxTokens": 8192, "maxSteps": 12, "temperature": 0.2,
                                            "scopeTagColors": {}, "scopeTags": ["payment"], "modelType": "amazon-nova-2-lite"}},
                "canvasMetadata": canvas(-935, -320, 227)},
        END: {"nodeId": END, "childNodes": [], "type": "terminate", "messages": [], "modalities": {},
              "metadata": {"stateModifications": [], "name": ""}, "canvasMetadata": canvas(-437, -314, 84)},
    },
}
out = Path(__file__).resolve().parents[1] / "acxd" / "ccg-renewal-payment.flow.json"
out.write_text(json.dumps(flow, indent=2) + "\n")
print("wrote", out)
