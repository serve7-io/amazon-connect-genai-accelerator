#!/usr/bin/env bash
# Copy the reviewed workflow sources in ci/workflows/ into .github/workflows/.
# Workflow files can only be changed by a person (or a token with the "workflow" scope); AI connectors usually
# cannot write them. So changes are made in ci/workflows/ through a normal PR, and a person installs them:
#
#   bash scripts/install-workflows.sh            # show what differs
#   bash scripts/install-workflows.sh --apply    # copy, commit on the current branch; then push / open a PR yourself
#
# Without git push rights for workflows: open each file in the GitHub web editor and paste the ci/workflows version.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
changed=()
for src in "$ROOT"/ci/workflows/*.yml; do
  dst="$ROOT/.github/workflows/$(basename "$src")"
  if ! cmp -s "$src" "$dst"; then
    changed+=("$(basename "$src")")
    echo "== $(basename "$src") differs"
    if [[ -f "$dst" ]]; then { diff -u "$dst" "$src" || true; } | head -40; else echo "   (new file)"; fi
  fi
done
if [[ ${#changed[@]} -eq 0 ]]; then
  echo "All workflows in .github/workflows match ci/workflows."
  exit 0
fi
if [[ "${1:-}" != "--apply" ]]; then
  echo; echo "Run with --apply to copy: ${changed[*]}"
  exit 0
fi
for f in "${changed[@]}"; do cp "$ROOT/ci/workflows/$f" "$ROOT/.github/workflows/$f"; done
git -C "$ROOT" add "${changed[@]/#/.github/workflows/}"
git -C "$ROOT" commit -m "Install workflows from ci/workflows: ${changed[*]}"
echo "Committed. Push the branch and open a PR (or push to main if you deploy straight from main)."
