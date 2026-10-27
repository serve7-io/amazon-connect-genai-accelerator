"""Maple Health demo admin commands (python3 demos/_kit/data.py maple-health <command> ...).

  set-agent <username> [routing profile name]   give an existing Connect user the "Maple Health Agents" routing profile
                                                (or the named one, to switch back). Prints the previous profile.
  inbox                                         the simulated email inbox (last 30 minutes, demo addresses only)
"""
import json

import boto3

INSTANCE = "78261fe2-310c-4f82-8437-4239487463ac"
REGION = "ca-central-1"


def _pages(fn, key, **kw):
    out = []
    while True:
        r = fn(**kw)
        out += r.get(key, [])
        if not r.get("NextToken"):
            return out
        kw["NextToken"] = r["NextToken"]


def set_agent(table, scan, username, *profile):
    name = " ".join(profile) or "Maple Health Agents"
    c = boto3.client("connect", region_name=REGION)
    users = {u["Username"]: u for u in _pages(c.list_users, "UserSummaryList", InstanceId=INSTANCE)}
    if username not in users:
        raise SystemExit(f"user {username} not found: create it in the Connect console first")
    rps = {r["Name"]: r for r in _pages(c.list_routing_profiles, "RoutingProfileSummaryList", InstanceId=INSTANCE)}
    if name not in rps:
        raise SystemExit(f"routing profile '{name}' not found (have: {', '.join(sorted(rps))})")
    uid = users[username]["Id"]
    old = c.describe_user(InstanceId=INSTANCE, UserId=uid)["User"]["RoutingProfileId"]
    old_name = next((n for n, r in rps.items() if r["Id"] == old), old)
    c.update_user_routing_profile(InstanceId=INSTANCE, UserId=uid, RoutingProfileId=rps[name]["Id"])
    print(f"{username}: routing profile '{old_name}' -> '{name}'")
    print(f"to switch back: demo: set-agent maple-health {username} {old_name}")


def inbox(table, scan):
    rows = sorted(scan(table("InboxTable")), key=lambda m: m["sent_at"], reverse=True)[:10]
    for m in rows:
        print(json.dumps({k: m[k] for k in ("sent_at", "to", "subject", "body")}, ensure_ascii=False))
    if not rows:
        print("inbox is empty")


COMMANDS = {"set-agent": set_agent, "inbox": inbox}
