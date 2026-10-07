#!/usr/bin/env python3
"""Pre-flight checks before any apply, and Bedrock model discovery.

- Fails fast if the region lacks Connect, root MFA is off (warning in dev), or no usable model exists.
- Picks the first model from tenant.yaml `model_preference` that is invokable in this account/region,
  resolving to a system inference profile when the model has no on-demand throughput.
- Writes stacks/40-genai/models.auto.tfvars.json with bedrock_model_id + bedrock_resource_arns.

Usage: python3 scripts/preflight.py --tenant sandbox [--region ca-central-1] [--skip-invoke-test]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: Path) -> dict:
    """Tiny YAML subset reader (key: value and '- item' lists) so CloudShell needs no PyYAML."""
    try:
        import yaml  # type: ignore
        return yaml.safe_load(path.read_text())
    except ImportError:
        data: dict = {}
        current = None
        for raw in path.read_text().splitlines():
            line = raw.split(" #")[0].rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            if line.startswith("  - ") and current:
                data.setdefault(current, []).append(line[4:].strip().strip('"'))
            elif not line.startswith(" ") and ":" in line:
                key, _, val = line.partition(":")
                current = key.strip()
                val = val.strip().strip('"')
                data[current] = val if val else None
        return data


def ok(msg: str) -> None:
    print(f"  [ok]   {msg}")


def warn(msg: str) -> None:
    print(f"  [warn] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    sys.exit(1)


def check_account(session: boto3.Session, environment: str) -> str:
    ident = session.client("sts").get_caller_identity()
    ok(f"account {ident['Account']} as {ident['Arn']}")
    try:
        summary = session.client("iam").get_account_summary()["SummaryMap"]
        if summary.get("AccountMFAEnabled") == 1:
            ok("root MFA enabled")
        elif environment == "prod":
            fail("root MFA is not enabled (ACCT-001)")
        else:
            warn("root MFA is not enabled - required before any client/prod use (ACCT-001)")
    except ClientError as err:
        warn(f"could not read IAM account summary: {err.response['Error']['Code']}")
    return ident["Account"]


def check_connect(session: boto3.Session) -> None:
    try:
        instances = session.client("connect").list_instances(MaxResults=10)["InstanceSummaryList"]
        ok(f"Amazon Connect reachable ({len(instances)} existing instance(s))")
    except ClientError as err:
        fail(f"Amazon Connect not usable in this region: {err.response['Error']['Code']}")


def _invoke_test(runtime, model_id: str) -> bool:
    try:
        runtime.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Reply with OK."}]}],
            inferenceConfig={"maxTokens": 5, "temperature": 0},
        )
        return True
    except ClientError as err:
        code = err.response["Error"]["Code"]
        msg = err.response["Error"].get("Message", "")[:140]
        warn(f"{model_id}: not invokable ({code}: {msg})")
        return False


def discover_model(session: boto3.Session, region: str, preference: list[str], pinned: str | None,
                   invoke_test: bool) -> tuple[str, list[str]]:
    bedrock = session.client("bedrock")
    runtime = session.client("bedrock-runtime")
    models = bedrock.list_foundation_models(byOutputModality="TEXT")["modelSummaries"]
    profiles = []
    paginator_token = None
    while True:
        kwargs = {"typeEquals": "SYSTEM_DEFINED", "maxResults": 100}
        if paginator_token:
            kwargs["nextToken"] = paginator_token
        resp = bedrock.list_inference_profiles(**kwargs)
        profiles.extend(resp.get("inferenceProfileSummaries", []))
        paginator_token = resp.get("nextToken")
        if not paginator_token:
            break

    print(f"  [info] {len(models)} text models and {len(profiles)} system inference profiles visible in {region}")

    def candidates_for(prefix: str):
        for m in models:
            if not m["modelId"].startswith(prefix) or m.get("modelLifecycle", {}).get("status") == "LEGACY":
                continue
            types = m.get("inferenceTypesSupported", [])
            if "ON_DEMAND" in types:
                yield m["modelId"], [m["modelArn"]]
            for p in profiles:
                if any(pm["modelArn"].endswith(f"/{m['modelId']}") for pm in p.get("models", [])):
                    # Prefer geographic profiles over global for residency
                    yield p["inferenceProfileId"], [p["inferenceProfileArn"]] + [pm["modelArn"] for pm in p["models"]]

    wanted = [pinned] if pinned else preference
    for prefix in wanted:
        cands = sorted(candidates_for(prefix), key=lambda c: c[0].startswith("global."))
        for model_id, arns in cands:
            if not invoke_test or _invoke_test(runtime, model_id):
                ok(f"model selected: {model_id}")
                return model_id, sorted(set(arns))
    fail("no model from model_preference is invokable here. Enable model access in the Bedrock console "
         "(Anthropic models need the one-time use-case form) or add another model to model_preference.")
    return "", []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--region")
    ap.add_argument("--skip-invoke-test", action="store_true")
    args = ap.parse_args()

    tenant = load_yaml(ROOT / "tenants" / args.tenant / "tenant.yaml")
    region = args.region or tenant["region"]
    session = boto3.Session(region_name=region)

    print(f"Pre-flight for tenant '{args.tenant}' in {region}")
    check_account(session, tenant.get("environment", "dev"))
    check_connect(session)
    model_id, arns = discover_model(session, region, tenant.get("model_preference") or [],
                                    tenant.get("bedrock_model_id"), not args.skip_invoke_test)

    out = ROOT / "stacks" / "40-genai" / "models.auto.tfvars.json"
    out.write_text(json.dumps({"bedrock_model_id": model_id, "bedrock_resource_arns": arns}, indent=2) + "\n")
    ok(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
