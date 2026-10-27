#!/usr/bin/env python3
"""auto-ship: merge an `auto/<name>` branch into main and tell the workflow which demos to deploy.

Run by .github/workflows/auto-ship.yml (source copy: demos/_kit/auto-ship.workflow.yml) on a push to `auto/**`:

    python3 demos/_kit/auto_ship.py ship

1. Merge the branch into a copy of the current main (nothing is pushed yet). A conflict means: needs review.
2. Gate: the merge must only touch demos/ (except demos/ccg-s5/), docs/ and README.md. Anything else (workflows, stacks,
   modules, scripts, IAM trust, bootstrap ...) needs a human: needs review.
3. Checks on the merged tree: every demo's tests + cfn-lint, the kit tests, the shared stack lint, shell syntax.
   A failure means: not shipped, the run is red.
4. Push the merge to main and delete the branch.
5. Output (to $GITHUB_OUTPUT): shipped=true|false, reason=..., demos="<space separated>".
   demos = AUTO_DEPLOY demos whose folder changed, or all of AUTO_DEPLOY when the shared kit (demos/_kit/) changed.

Environment: GITHUB_REF_NAME (branch), GITHUB_SHA (its tip), AUTO_DEPLOY (default "renault-rental"), GITHUB_OUTPUT.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = (re.compile(r"^demos/"), re.compile(r"^docs/"), re.compile(r"^README\.md$"))
BLOCKED = (re.compile(r"^demos/ccg-s5/"),)   # has its own workflow, and demos/ccg-s5/ci is the CI role's IAM trust


def classify(changed):
    """-> (allowed, reason)."""
    if not changed:
        return False, "the branch changes nothing compared with main"
    bad = [f for f in changed if any(b.match(f) for b in BLOCKED) or not any(a.match(f) for a in ALLOWED)]
    if bad:
        shown = ", ".join(bad[:5]) + (f" and {len(bad) - 5} more" if len(bad) > 5 else "")
        return False, f"touches files that need a human review: {shown}"
    return True, "only demos/, docs/ and README.md changed"


def demos_to_deploy(changed, auto_deploy, root=ROOT):
    wanted = set(auto_deploy)
    touched = set()
    for f in changed:
        parts = f.split("/")
        if parts[0] == "demos" and len(parts) >= 3:
            if parts[1] == "_kit":
                touched |= wanted                      # the kit is shared: every auto-deployed demo is affected
            elif (Path(root) / "demos" / parts[1] / "demo.json").is_file():
                touched.add(parts[1])
    return sorted(touched & wanted)


def git(*args, root=ROOT, check=True):
    return subprocess.run(["git", *args], cwd=root, check=check, capture_output=True, text=True)


def run_checks(root=ROOT):
    """-> list of failure descriptions (empty = all good). Output is streamed so it shows in the workflow log."""
    root = Path(root)
    failures = []

    def run(label, cmd):
        print(f"\n== {label}: {' '.join(cmd)}", flush=True)
        if subprocess.run(cmd, cwd=root).returncode != 0:
            failures.append(label)

    for demo_json in sorted(root.glob("demos/*/demo.json")):
        d = demo_json.parent
        if (d / "tests").is_dir():
            run(f"tests {d.name}", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"demos/{d.name}/tests"])
        if (d / "template.yaml").is_file():
            run(f"cfn-lint {d.name}", ["cfn-lint", f"demos/{d.name}/template.yaml"])
    if (root / "demos/_kit/tests").is_dir():
        run("kit tests", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "demos/_kit/tests"])
    if (root / "demos/_kit/web/stack.yaml").is_file():
        run("cfn-lint shared web stack", ["cfn-lint", "demos/_kit/web/stack.yaml"])
    for sh in sorted(list(root.glob("demos/_kit/*.sh")) + list(root.glob("demos/_kit/web/*.sh"))):
        run(f"bash -n {sh.name}", ["bash", "-n", str(sh)])
    return failures


def emit(**kv):
    lines = "".join(f"{k}={str(v).replace(chr(10), ' ')}\n" for k, v in kv.items())
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(lines)
    print(lines, end="")


def ship(root=ROOT, checks=run_checks, env=None):
    """-> exit code (0 = shipped or needs review, 1 = checks failed or main moved)."""
    env = os.environ if env is None else env
    branch, sha = env["GITHUB_REF_NAME"], env["GITHUB_SHA"]
    auto_deploy = (env.get("AUTO_DEPLOY") or "renault-rental").split()
    git("fetch", "origin", "main", root=root)
    git("checkout", "-q", "-B", "ship", "origin/main", root=root)
    merged = git("merge", "--no-ff", "-m", f"auto-ship: merge {branch}", sha, root=root, check=False)
    if merged.returncode != 0:
        git("merge", "--abort", root=root, check=False)
        emit(shipped="false", reason="merge conflict with main, needs a human")
        return 0
    changed = [f for f in git("diff", "--name-only", "origin/main", "HEAD", root=root).stdout.split("\n") if f]
    allowed, reason = classify(changed)
    if not allowed:
        emit(shipped="false", reason=reason)
        return 0
    failures = checks(root)
    if failures:
        emit(shipped="false", reason="checks failed: " + ", ".join(failures))
        return 1
    pushed = git("push", "origin", "HEAD:main", root=root, check=False)
    if pushed.returncode != 0:
        emit(shipped="false", reason="main moved while the checks ran; push the branch again")
        return 1
    git("push", "origin", "--delete", branch, root=root, check=False)
    deploy = demos_to_deploy(changed, auto_deploy, root)
    emit(shipped="true", reason=reason, demos=" ".join(deploy))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["ship"]:
        sys.exit(__doc__)
    sys.exit(ship())
