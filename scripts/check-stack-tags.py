#!/usr/bin/env python3
"""CI guard: every aws_cloudformation_stack must ignore tag changes.

Stack tags propagate to the resources inside. Several Connect / Q in Connect resources (AWS::Wisdom::KnowledgeBase,
AssistantAssociation, ...) cannot change tags in place, so a changed default tag (e.g. serve7:release) makes the
stack update fail and roll back (s7-sandbox-dev-qic-selfservice, 7 Oct 2026). Exit 1 if a stack lacks
`lifecycle { ignore_changes = [tags, tags_all] }`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
bad = []
for tf in sorted((ROOT / "stacks").rglob("*.tf")):
    text = tf.read_text()
    for m in re.finditer(r'^resource "aws_cloudformation_stack" "([^"]+)" \{', text, re.MULTILINE):
        end = re.search(r"^\}", text[m.end():], re.MULTILINE)
        block = text[m.end(): m.end() + (end.start() if end else len(text))]
        if not re.search(r"ignore_changes\s*=\s*\[\s*tags\s*,\s*tags_all\s*\]", block):
            bad.append(f"{tf.relative_to(ROOT)}: aws_cloudformation_stack.{m.group(1)}")
if bad:
    print("These CloudFormation stacks must ignore tag changes (lifecycle { ignore_changes = [tags, tags_all] }):")
    print("\n".join(f"  {b}" for b in bad))
    sys.exit(1)
print("all aws_cloudformation_stack resources ignore tag changes")
