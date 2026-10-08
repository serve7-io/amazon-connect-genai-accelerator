#!/usr/bin/env python3
"""Finish the self-service AI agent after layer 40 (steps CloudFormation has no resource for).

    python3 scripts/configure-ai-agent.py --tenant sandbox [--env dev]

1. Link the AI agent to its security profile (connect:AssociateSecurityProfiles, entity type AI_AGENT).
   The profile is what allows the agent to call the CRM tools.
2. Make the newest published agent version the assistant's self-service orchestration agent
   (qconnect:UpdateAssistantAIAgent, use case Connect.SelfService).

Idempotent; reads everything from the s7-<tenant>-<env>-ai-agent stack. Run by deploy.sh with the deploy role.
"""
from __future__ import annotations

import argparse
import sys

import boto3
from botocore.exceptions import ClientError


def outputs(cfn, stack: str) -> dict[str, str] | None:
    try:
        s = cfn.describe_stacks(StackName=stack)["Stacks"][0]
    except ClientError as err:
        if "does not exist" in str(err):
            return None
        raise
    return {o["OutputKey"]: o["OutputValue"] for o in s.get("Outputs", [])} | {
        p["ParameterKey"]: p["ParameterValue"] for p in s.get("Parameters", [])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--env", default="dev")
    a = ap.parse_args()
    prefix = f"s7-{a.tenant}-{a.env}"
    session = boto3.session.Session()
    o = outputs(session.client("cloudformation"), f"{prefix}-ai-agent")
    if not o:
        print(f"{prefix}-ai-agent not deployed (enable_ai_agent/enable_agentic_tools off) - skipping")
        return
    connect, qc = session.client("connect"), session.client("qconnect")
    if not hasattr(connect, "associate_security_profiles") or not hasattr(qc, "update_assistant_ai_agent"):
        sys.exit("boto3 is too old for AI agent APIs: pip install --upgrade boto3")

    instance_id = o["ConnectInstanceArn"].rsplit("/", 1)[1]
    assistant_id, agent_id = o["AssistantId"], o["AgentId"]
    profile_id = o["SecurityProfileArn"].rsplit("/", 1)[1]
    head, tail = o["AgentArn"].rsplit("/", 1)
    agent_arn = f"{head}/{tail.split(':')[0]}"          # unqualified (no :$LATEST / :N)

    # 1. security profile <-> agent
    linked = {p["Id"] for p in connect.list_entity_security_profiles(
        InstanceId=instance_id, EntityType="AI_AGENT", EntityArn=agent_arn).get("SecurityProfiles", [])}
    if profile_id in linked:
        print(f"security profile {profile_id} already linked to {agent_id}")
    else:
        connect.associate_security_profiles(InstanceId=instance_id, EntityType="AI_AGENT", EntityArn=agent_arn,
                                            SecurityProfiles=[{"Id": profile_id}])
        print(f"linked security profile {profile_id} to AI agent {agent_id}")

    # 2. newest version -> assistant default for self-service
    versions = qc.list_ai_agent_versions(assistantId=assistant_id, aiAgentId=agent_id).get("aiAgentVersionSummaries", [])
    if not versions:
        sys.exit(f"AI agent {agent_id} has no published version")
    latest = max(v["versionNumber"] for v in versions)
    want = f"{agent_id}:{latest}"
    current = (qc.get_assistant(assistantId=assistant_id)["assistant"].get("aiAgentConfiguration", {})
               .get("ORCHESTRATION", {}).get("aiAgentId"))
    if current == want:
        print(f"assistant already uses {want}")
    else:
        qc.update_assistant_ai_agent(assistantId=assistant_id, aiAgentType="ORCHESTRATION",
                                     orchestratorUseCase="Connect.SelfService", configuration={"aiAgentId": want})
        print(f"assistant self-service agent: {current} -> {want}")


if __name__ == "__main__":
    try:
        main()
    except ClientError as err:
        sys.exit(f"configure-ai-agent failed: {err.response['Error']['Code']}: {err.response['Error']['Message']}")
