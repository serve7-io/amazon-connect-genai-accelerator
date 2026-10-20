"""Tests for demos/_kit/auto_ship.py with real local git repositories (a bare origin and a working clone)."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("auto_ship", KIT / "auto_ship.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


auto_ship = _load()


def sh(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def write(root, files):
    for path, text in files.items():
        p = Path(root) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path):
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    sh(work, "config", "user.name", "test")
    sh(work, "config", "user.email", "test@example.com")
    sh(work, "checkout", "-q", "-b", "main")
    write(work, {"demos/renault-rental/demo.json": "{}\n", "docs/shared.md": "line one\n", "README.md": "readme\n"})
    sh(work, "add", "-A")
    sh(work, "commit", "-q", "-m", "base")
    sh(work, "push", "-q", "origin", "main")
    return work


def branch(work, name, files):
    """Create `name` from origin/main with `files`, push it, return its tip sha. Leaves the clone on main."""
    sh(work, "fetch", "-q", "origin")
    sh(work, "checkout", "-q", "-B", name, "origin/main")
    write(work, files)
    sh(work, "add", "-A")
    sh(work, "commit", "-q", "-m", f"change on {name}")
    sh(work, "push", "-q", "origin", name)
    tip = sh(work, "rev-parse", "HEAD")
    sh(work, "checkout", "-q", "main")
    return tip


def advance_main(work, files):
    sh(work, "fetch", "-q", "origin")
    sh(work, "checkout", "-q", "-B", "main", "origin/main")
    write(work, files)
    sh(work, "add", "-A")
    sh(work, "commit", "-q", "-m", "main moved")
    sh(work, "push", "-q", "origin", "main")


def origin_main(work):
    return sh(work, "ls-remote", "origin", "refs/heads/main").split()[0]


def remote_has(work, name):
    return bool(sh(work, "ls-remote", "origin", f"refs/heads/{name}"))


@pytest.fixture()
def run(repo, monkeypatch):
    out = {}
    monkeypatch.setattr(auto_ship, "emit", lambda **kv: out.update(kv))

    def _run(name, sha, checks=lambda root: [], auto_deploy="renault-rental"):
        out.clear()
        code = auto_ship.ship(root=repo, checks=checks,
                              env={"GITHUB_REF_NAME": name, "GITHUB_SHA": sha, "AUTO_DEPLOY": auto_deploy})
        return code, dict(out)
    return _run


@pytest.mark.parametrize("changed", [
    ["docs/note.md"], ["README.md"], ["demos/renault-rental/README.md"],
    ["demos/_kit/deploy.sh", "demos/renault-rental/web/actions.js"],
])
def test_classify_allows_demos_docs_and_readme(changed):
    ok, reason = auto_ship.classify(changed)
    assert ok and reason


@pytest.mark.parametrize("changed", [
    [], [".github/workflows/demo.yml"], ["stacks/x/main.tf"], ["modules/m/main.tf"], ["scripts/x.sh"],
    ["bootstrap/oidc.tf"], ["demos/ccg-s5/ci/github-trust.yaml"], ["demos/ccg-s5/scripts/main-flow.sh"],
    ["docs/ok.md", "lambdas/x/handler.py"], ["README.md.bak"], ["tenants/acme/vars.tfvars"],
])
def test_classify_blocks_everything_else(changed):
    ok, reason = auto_ship.classify(changed)
    assert not ok and reason


def test_classify_names_only_the_offending_files():
    ok, reason = auto_ship.classify(["docs/ok.md", "lambdas/x/handler.py"])
    assert not ok and "lambdas/x/handler.py" in reason and "docs/ok.md" not in reason


def test_demos_to_deploy(tmp_path):
    for d in ("renault-rental", "ccg-s5", "yamaha-moto"):
        write(tmp_path, {f"demos/{d}/demo.json": "{}"})
    ad = ["renault-rental", "yamaha-moto"]
    assert auto_ship.demos_to_deploy(["demos/renault-rental/web/a.js"], ad, tmp_path) == ["renault-rental"]
    assert auto_ship.demos_to_deploy(["demos/ccg-s5/x.py", "docs/a.md"], ad, tmp_path) == []
    assert auto_ship.demos_to_deploy(["demos/notademo/x.py"], ad, tmp_path) == []
    assert auto_ship.demos_to_deploy(["demos/_kit/deploy.sh"], ad, tmp_path) == ["renault-rental", "yamaha-moto"]


def test_docs_only_branch_is_merged_and_deleted(repo, run):
    sha = branch(repo, "auto/docs", {"docs/new.md": "hello\n"})
    code, out = run("auto/docs", sha)
    assert code == 0 and out["shipped"] == "true" and out["demos"] == ""
    assert not remote_has(repo, "auto/docs")
    sh(repo, "fetch", "-q", "origin")
    assert sh(repo, "log", "-1", "--format=%s", "origin/main").startswith("auto-ship: merge auto/docs")
    assert sh(repo, "show", "origin/main:docs/new.md") == "hello"


def test_demo_change_asks_for_that_demo(repo, run):
    sha = branch(repo, "auto/renault", {"demos/renault-rental/README.md": "x\n"})
    code, out = run("auto/renault", sha)
    assert code == 0 and out["shipped"] == "true" and out["demos"] == "renault-rental"


def test_kit_change_deploys_every_auto_deploy_demo(repo, run):
    write(repo, {})
    sha = branch(repo, "auto/kit", {"demos/_kit/x.py": "x = 1\n"})
    code, out = run("auto/kit", sha)
    assert out["shipped"] == "true" and out["demos"] == "renault-rental"


def test_workflow_change_is_not_shipped(repo, run):
    before = origin_main(repo)
    sha = branch(repo, "auto/evil", {".github/workflows/evil.yml": "on: push\n"})
    code, out = run("auto/evil", sha)
    assert code == 0 and out["shipped"] == "false" and "evil.yml" in out["reason"]
    assert origin_main(repo) == before and remote_has(repo, "auto/evil")


def test_ccg_s5_change_is_not_shipped(repo, run):
    before = origin_main(repo)
    sha = branch(repo, "auto/s5", {"demos/ccg-s5/ci/trust.yaml": "x\n"})
    code, out = run("auto/s5", sha)
    assert out["shipped"] == "false" and origin_main(repo) == before


def test_merge_conflict_is_not_shipped(repo, run):
    sha = branch(repo, "auto/conflict", {"docs/shared.md": "branch version\n"})
    advance_main(repo, {"docs/shared.md": "main version\n"})
    before = origin_main(repo)
    code, out = run("auto/conflict", sha)
    assert code == 0 and out["shipped"] == "false" and "conflict" in out["reason"]
    assert origin_main(repo) == before


def test_failing_checks_stop_the_merge(repo, run):
    before = origin_main(repo)
    sha = branch(repo, "auto/broken", {"docs/x.md": "x\n"})
    code, out = run("auto/broken", sha, checks=lambda root: ["tests renault-rental"])
    assert code == 1 and out["shipped"] == "false" and "tests renault-rental" in out["reason"]
    assert origin_main(repo) == before and remote_has(repo, "auto/broken")


def test_checks_run_on_the_merged_tree(repo, run):
    sha = branch(repo, "auto/merged", {"docs/from-branch.md": "b\n"})
    advance_main(repo, {"docs/from-main.md": "m\n"})
    seen = {}

    def spy(root):
        seen["branch"] = (Path(root) / "docs/from-branch.md").is_file()
        seen["main"] = (Path(root) / "docs/from-main.md").is_file()
        return []
    code, out = run("auto/merged", sha, checks=spy)
    assert out["shipped"] == "true" and seen == {"branch": True, "main": True}
