#!/usr/bin/env python3
"""Fetch the AWS-managed orchestration prompt once, so tenants can get their own persona on top of it.

    python3 scripts/fetch-orchestration-prompt.py --tenant sandbox [--env dev] [--prompt-id <id:version>]

Reads the prompt the deployed AI agent uses (SelfServiceOrchestrationChat by default) with qconnect:GetAIPrompt and
writes modules/ai-selfservice-agent/prompts/orchestration-chat.base.json:

    text        the prompt text, with a marker line where a tenant persona goes (start of the system message)
    marker      the marker string; stacks/40-genai/agent.tf replaces it with tenants/<t>/agent.yaml persona
    indent      indentation of the system message, applied to every persona line
    model_id, api_format, source_prompt_id, fetched_at

Commit that file (it is AWS's published template, no customer data). Re-run only when moving to a newer AWS prompt
version. Needs a session in an account where layer 40 with the AI agent is deployed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "modules" / "ai-selfservice-agent" / "prompts" / "orchestration-chat.base.json"
MARKER = "<<SERVE7_PERSONA>>"
SYSTEM_RE = re.compile(r"^(?P<indent>\s*)system:\s*[|>][-+]?\s*$")


def insert_marker(text: str) -> tuple[str, str]:
    """Put MARKER as the first line of the system message; return (text, indent of that message)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if SYSTEM_RE.match(line):
            body = next((l for l in lines[i + 1:] if l.strip()), "")
            indent = body[: len(body) - len(body.lstrip())]
            if len(indent) <= len(SYSTEM_RE.match(line)["indent"]):
                break
            lines[i + 1:i + 1] = [MARKER, ""]
            return "\n".join(lines), indent
    raise ValueError("no 'system: |' block found")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--env", default="dev")
    ap.add_argument("--prompt-id", help="id:version (default: the one the deployed agent stack uses)")
    a = ap.parse_args()
    prefix = f"s7-{a.tenant}-{a.env}"
    s = boto3.session.Session()
    stack = s.client("cloudformation").describe_stacks(StackName=f"{prefix}-ai-agent")["Stacks"][0]
    params = {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}
    prompt_id = a.prompt_id or params["OrchestrationPromptId"]
    p = s.client("qconnect").get_ai_prompt(assistantId=params["AssistantId"], aiPromptId=prompt_id)["aiPrompt"]
    raw = p["templateConfiguration"]["textFullAIPromptEditTemplateConfiguration"]["text"]
    raw_path = OUT.with_suffix(".raw.txt")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw)
    try:
        text, indent = insert_marker(raw)
    except ValueError as err:
        sys.exit(f"{err}: the prompt format is not the expected YAML messages template. "
                 f"Raw text saved to {raw_path.relative_to(ROOT)} - send it for review; nothing else written.")
    OUT.write_text(json.dumps({
        "source_prompt_id": prompt_id, "source_prompt_name": p.get("name"), "model_id": p["modelId"],
        "api_format": p["apiFormat"], "marker": MARKER, "indent": indent,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "text": text,
    }, indent=2) + "\n")
    print(f"Wrote {OUT.relative_to(ROOT)} ({p.get('name')}, {p['modelId']}, {len(raw)} chars; raw copy {raw_path.name}).")
    print("Commit both files. Tenants with a persona in agent.yaml get their own prompt on the next deploy.")


if __name__ == "__main__":
    main()
