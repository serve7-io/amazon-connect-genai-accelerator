#!/usr/bin/env python3
"""Upload tenants/<tenant>/knowledge/* into the tenant's Amazon Q in Connect knowledge base.

Creates new content or replaces existing content with the same name. Idempotent.
    python3 scripts/sync-knowledge.py --tenant sandbox --env dev
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
TYPES = {
    ".txt": "text/plain",
    ".html": "text/html",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def region_of(tenant: str) -> str:
    for line in (ROOT / "tenants" / tenant / "tenant.yaml").read_text().splitlines():
        if line.startswith("region:"):
            return line.split(":", 1)[1].split("#")[0].strip()
    raise SystemExit("region missing in tenant.yaml")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--env", default="dev")
    args = ap.parse_args()
    region = region_of(args.tenant)
    kb_name = f"s7-{args.tenant}-{args.env}-kb"
    qc = boto3.client("qconnect", region_name=region)

    kbs = [k for k in qc.list_knowledge_bases()["knowledgeBaseSummaries"] if k["name"] == kb_name]
    if not kbs:
        print(f"Knowledge base {kb_name} not found - is self-service enabled and layer 40 applied?")
        sys.exit(1)
    kb_id = kbs[0]["knowledgeBaseId"]

    existing = {}
    paginator_token = None
    while True:
        kwargs = {"knowledgeBaseId": kb_id, "maxResults": 100}
        if paginator_token:
            kwargs["nextToken"] = paginator_token
        resp = qc.list_contents(**kwargs)
        existing.update({c["name"]: c["contentId"] for c in resp["contentSummaries"]})
        paginator_token = resp.get("nextToken")
        if not paginator_token:
            break

    folder = ROOT / "tenants" / args.tenant / "knowledge"
    files = sorted(p for p in folder.glob("*") if p.suffix.lower() in TYPES)
    if not files:
        print(f"No knowledge files in {folder}")
        return

    for path in files:
        ctype = TYPES[path.suffix.lower()]
        up = qc.start_content_upload(knowledgeBaseId=kb_id, contentType=ctype)
        req = urllib.request.Request(up["url"], data=path.read_bytes(), method="PUT",
                                     headers=up.get("headersToInclude", {}))
        with urllib.request.urlopen(req) as r:  # noqa: S310 - presigned S3 URL from AWS
            if r.status not in (200, 204):
                raise SystemExit(f"upload of {path.name} failed: HTTP {r.status}")
        if path.name in existing:
            qc.update_content(knowledgeBaseId=kb_id, contentId=existing[path.name], uploadId=up["uploadId"])
            print(f"  updated  {path.name}")
        else:
            qc.create_content(knowledgeBaseId=kb_id, name=path.name, uploadId=up["uploadId"],
                              metadata={"tenant": args.tenant, "source": "repo"})
            print(f"  created  {path.name}")
    print(f"Synced {len(files)} file(s) to {kb_name} ({kb_id}). Indexing takes a few minutes.")


if __name__ == "__main__":
    main()
