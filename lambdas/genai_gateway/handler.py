"""Serve7 GenAI gateway - the only path from Amazon Connect flows to Amazon Bedrock.

Contract (guide section 3.3):
- The flow passes only `action` plus contact attributes; prompts live in ./prompts and are versioned.
- Every model call carries the tenant guardrail (numbered version, trace disabled) and an explicit maxTokens.
- Untrusted text (caller utterances, transcripts) is wrapped in guardContent blocks.
- Must answer inside Connect's 8 s limit: on throttling/timeout we return route_hint=agent, never an error.
- Returns a flat string map so Connect can branch on $.External.<key>.
- Logs only contactId, action, latency, token counts and guardrailAction - never customer text.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)

PROMPTS_DIR = Path(__file__).parent / "prompts"
ALLOWED_INTENTS = {"billing", "technical_support", "account", "sales", "complaint", "other"}
ROUTE_FOR_INTENT = {"billing": "billing"}

_BEDROCK_CFG = Config(
    connect_timeout=1,
    read_timeout=3,
    retries={"max_attempts": 2, "mode": "adaptive"},
)

_bedrock = None
_ddb = None
_cw = None


def _clients():
    global _bedrock, _ddb, _cw
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", config=_BEDROCK_CFG)
        _ddb = boto3.client("dynamodb")
        _cw = boto3.client("cloudwatch")
    return _bedrock, _ddb, _cw


def load_prompt(action: str) -> dict[str, Any]:
    path = PROMPTS_DIR / f"{action}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _guardrail_config() -> dict[str, str]:
    return {
        "guardrailIdentifier": os.environ["GUARDRAIL_ID"],
        "guardrailVersion": os.environ["GUARDRAIL_VERSION"],
        "trace": "disabled",
    }


def build_classify_request(utterance: str, prompt: dict[str, Any]) -> dict[str, Any]:
    """Few-shot classification. System prompt is trusted; the utterance goes in guardContent."""
    shots = "\n".join(f"Caller: {s['text']}\nIntent: {s['intent']}" for s in prompt["examples"])
    system_text = f"{prompt['system']}\n\nExamples:\n{shots}"
    return {
        "modelId": os.environ["MODEL_ID"],
        "system": [{"text": system_text}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"text": "Classify this caller message. Reply with JSON only."},
                    {"guardContent": {"text": {"text": utterance}}},
                ],
            }
        ],
        "inferenceConfig": {"maxTokens": prompt["max_tokens"], "temperature": 0},
        "guardrailConfig": _guardrail_config(),
    }


def parse_classification(text: str) -> tuple[str, float]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return "other", 0.0
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return "other", 0.0
    intent = str(data.get("intent", "other")).lower()
    if intent not in ALLOWED_INTENTS:
        intent = "other"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return intent, confidence


def _metric(name: str, value: float = 1, unit: str = "Count") -> None:
    try:
        _, _, cw = _clients()
        cw.put_metric_data(
            Namespace=os.environ.get("METRIC_NAMESPACE", "Serve7/GenAIGateway"),
            MetricData=[{
                "MetricName": name,
                "Dimensions": [{"Name": "Tenant", "Value": os.environ.get("TENANT", "unknown")}],
                "Value": value,
                "Unit": unit,
            }],
        )
    except (BotoCoreError, ClientError):  # metrics must never break a call
        LOG.warning("metric_publish_failed", extra={"metric": name})


def _save_state(contact_id: str, fields: dict[str, str]) -> None:
    _, ddb, _ = _clients()
    ttl_days = int(os.environ.get("STATE_TTL_DAYS", "30"))
    item = {"contactId": {"S": contact_id}, "expiresAt": {"N": str(int(time.time()) + ttl_days * 86400)}}
    item.update({k: {"S": v} for k, v in fields.items()})
    ddb.put_item(TableName=os.environ["STATE_TABLE"], Item=item)


def fallback(reason: str) -> dict[str, str]:
    _metric("Fallback")
    return {"intent": "other", "confidence": "0", "route_hint": "agent", "guardrailAction": "NONE", "status": reason}


def classify(contact_id: str, utterance: str) -> dict[str, str]:
    if not utterance.strip():
        return {"intent": "other", "confidence": "0", "route_hint": "general", "guardrailAction": "NONE", "status": "no_input"}

    prompt = load_prompt("classify")
    bedrock, _, _ = _clients()
    started = time.monotonic()
    try:
        resp = bedrock.converse(**build_classify_request(utterance, prompt))
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "ClientError")
        LOG.warning("bedrock_error", extra={"contactId": contact_id, "code": code})
        return fallback(code)
    except BotoCoreError as err:  # timeouts, connection errors
        LOG.warning("bedrock_unavailable", extra={"contactId": contact_id, "error": type(err).__name__})
        return fallback("timeout")

    latency_ms = int((time.monotonic() - started) * 1000)
    usage = resp.get("usage", {})
    guardrail_action = "INTERVENED" if resp.get("stopReason") == "guardrail_intervened" else "NONE"

    _metric("InputTokens", usage.get("inputTokens", 0))
    _metric("OutputTokens", usage.get("outputTokens", 0))
    if guardrail_action == "INTERVENED":
        _metric("GuardrailIntervened")
        result = {"intent": "other", "confidence": "0", "route_hint": "agent", "guardrailAction": guardrail_action, "status": "ok"}
    else:
        text = "".join(part.get("text", "") for part in resp["output"]["message"]["content"])
        intent, confidence = parse_classification(text)
        threshold = float(prompt.get("min_confidence", 0.6))
        route = ROUTE_FOR_INTENT.get(intent, "general") if confidence >= threshold else "agent"
        result = {
            "intent": intent,
            "confidence": f"{confidence:.2f}",
            "route_hint": route,
            "guardrailAction": guardrail_action,
            "status": "ok",
        }

    LOG.info("classified", extra={
        "contactId": contact_id, "action": "classify", "latency_ms": latency_ms,
        "input_tokens": usage.get("inputTokens"), "output_tokens": usage.get("outputTokens"),
        "guardrailAction": guardrail_action, "intent": result["intent"], "prompt_version": prompt["version"],
    })
    try:
        _save_state(contact_id, {"intent": result["intent"], "routeHint": result["route_hint"],
                                 "guardrailAction": guardrail_action, "promptVersion": prompt["version"]})
    except (BotoCoreError, ClientError):
        LOG.warning("state_save_failed", extra={"contactId": contact_id})
    return result


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, str]:
    details = event.get("Details", {})
    params = details.get("Parameters", {}) or {}
    contact = details.get("ContactData", {}) or {}
    attributes = contact.get("Attributes", {}) or {}
    contact_id = contact.get("ContactId", "unknown")
    action = params.get("action", "classify")

    if action == "classify":
        utterance = params.get("utterance") or attributes.get("utterance", "")
        return classify(contact_id, utterance[:2000])

    LOG.warning("unknown_action", extra={"contactId": contact_id, "action": action})
    return fallback("unknown_action")
