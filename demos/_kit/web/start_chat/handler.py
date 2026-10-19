"""Demo kit web chat: browser-facing StartChatContact endpoint behind CloudFront (Lambda function URL).

POST /<demo>/start-chat   (called by the Amazon Connect Touchpoint web chat on the demo's test page)
POST /<demo>/start-voice  (same, for the Voice / Voice mini input: starts an in-browser WebRTC voice contact)

Keyless by design (like the AWS StartChatContact sample) but locked down:
  - the demo name comes from the URL path and must match the demo-name pattern;
  - the Connect instance and flow are read from SSM  /demo-kit/web/<demo>/chat  (or .../voice for start-voice), written
    by deploy-web.sh, so only demos that were deployed through the kit can start a contact, and only into their own flow;
  - whatever else the browser sends is ignored, except the display name (sanitised) and the message content types
    (filtered to an allow-list);
  - the only attribute set is demo_entry=<demo>.
Response shapes are what Touchpoint expects:
  start-chat : {"data": {"startChatResult": {ContactId, ParticipantId, ParticipantToken}}}
  start-voice: {"data": {"startWebRtcResult": {ConnectionData: {Meeting, Attendee}, ContactId, ParticipantId, ParticipantToken}}}
"""
import json
import os
import re
import time

DEMO_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
ALLOWED_CONTENT_TYPES = ["text/plain", "text/markdown", "application/json",
                         "application/vnd.amazonaws.connect.message.interactive",
                         "application/vnd.amazonaws.connect.message.interactive.response"]
CACHE_SECONDS = 60

SSM = CONNECT = None          # boto3 clients (created lazily; replaced by fakes in tests)
_CACHE = {}                   # demo -> (expires_at, config)


def _ssm():
    global SSM
    if SSM is None:
        import boto3
        SSM = boto3.client("ssm")
    return SSM


def _connect():
    global CONNECT
    if CONNECT is None:
        import boto3
        CONNECT = boto3.client("connect")
    return CONNECT


def _demo_config(demo, kind="chat"):
    """-> {"instance_id": ..., "flow_id": ...} or None when the demo has no chat/voice wired yet."""
    key = demo if kind == "chat" else f"{demo}:{kind}"
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        raw = _ssm().get_parameter(Name=f"/demo-kit/web/{demo}/{kind}")["Parameter"]["Value"]
        cfg = json.loads(raw)
        cfg = cfg if cfg.get("instance_id") and cfg.get("flow_id") else None
    except Exception as e:  # noqa: BLE001 - missing parameter and bad JSON both mean "not ready"
        if type(e).__name__ not in ("ParameterNotFound", "JSONDecodeError"):
            print({"ssm_error": type(e).__name__})
        cfg = None
    _CACHE[key] = (time.time() + CACHE_SECONDS, cfg)
    return cfg


def _resp(code, body):
    return {"statusCode": code, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(body, default=str)}


def handler(event, _ctx):
    http = (event.get("requestContext") or {}).get("http") or {}
    method = (http.get("method") or event.get("httpMethod") or "").upper()
    parts = [p for p in (event.get("rawPath") or "").split("/") if p]
    if len(parts) != 2 or parts[1] not in ("start-chat", "start-voice") or not DEMO_RE.fullmatch(parts[0]):
        return _resp(404, {"error": "not_found"})
    if method != "POST":
        return _resp(405, {"error": "method_not_allowed"})
    demo = parts[0]
    voice = parts[1] == "start-voice"
    cfg = _demo_config(demo, "voice" if voice else "chat")
    if not cfg:
        return _resp(503, {"error": "voice_not_ready" if voice else "chat_not_ready",
                           "message": ("Voice" if voice else "Chat") + " is not available yet."})

    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    try:
        body = json.loads(raw)
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    details = body.get("ParticipantDetails") if isinstance(body.get("ParticipantDetails"), dict) else {}
    name = re.sub(r"[^\w .'-]", "", str(details.get("DisplayName") or "Customer"))[:40].strip() or "Customer"
    if voice:
        r = _connect().start_web_rtc_contact(InstanceId=cfg["instance_id"], ContactFlowId=cfg["flow_id"],
                                             ParticipantDetails={"DisplayName": name}, Attributes={"demo_entry": demo})
        print({"start_voice": "ok", "demo": demo, "contact_id": r["ContactId"]})
        return _resp(200, {"data": {"startWebRtcResult": {
            "ConnectionData": r["ConnectionData"], "ContactId": r["ContactId"],
            "ParticipantId": r["ParticipantId"], "ParticipantToken": r["ParticipantToken"]}}})
    asked = body.get("SupportedMessagingContentTypes")
    types = [t for t in (asked if isinstance(asked, list) else ALLOWED_CONTENT_TYPES) if t in ALLOWED_CONTENT_TYPES]
    if "text/plain" not in types:
        types = ["text/plain"] + types

    r = _connect().start_chat_contact(InstanceId=cfg["instance_id"], ContactFlowId=cfg["flow_id"],
                                      ParticipantDetails={"DisplayName": name}, Attributes={"demo_entry": demo},
                                      SupportedMessagingContentTypes=types)
    print({"start_chat": "ok", "demo": demo, "contact_id": r["ContactId"]})
    return _resp(200, {"data": {"startChatResult": {"ContactId": r["ContactId"], "ParticipantId": r["ParticipantId"],
                                                    "ParticipantToken": r["ParticipantToken"]}}})
