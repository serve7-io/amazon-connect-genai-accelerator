"""Unit tests for the new-org tooling: scripts/ci-tenants.py and scripts/org-setup.py (no AWS, no GitHub)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ci = _load("ci-tenants")
org = _load("org-setup")


def _tenant(root, name, account, envs=("dev",), extra=""):
    d = root / "tenants" / name
    d.mkdir(parents=True)
    d.joinpath("tenant.yaml").write_text(f'tenant: {name}\nenvironment: dev\naccount_id: "{account}"\nregion: ca-central-1\n{extra}')
    for e in envs:
        d.joinpath(f"{e}.tfvars").write_text("")


def _repo(tmp_path):
    _tenant(tmp_path, "sandbox", "872376836480")
    _tenant(tmp_path, "acme", "123456789012", envs=("dev", "prod"),
            extra='environments:\n  prod:\n    account_id: "210987654321"\n')
    (tmp_path / "ci" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    for n in org.WORKFLOWS:
        (tmp_path / "ci" / "workflows" / n).write_text(n)
        (tmp_path / ".github" / "workflows" / n).write_text(n)
    return tmp_path


# ------------------------------------------------------------------ ci-tenants
def test_default_is_sandbox_and_variables_override(tmp_path):
    root = _repo(tmp_path)
    assert ci.resolve("", "sandbox", "dev", False, root)[0] == [{"tenant": "sandbox", "env": "dev"}]
    assert ci.resolve("", "acme", "dev", False, root)[0] == [{"tenant": "acme", "env": "dev"}]
    items, _ = ci.resolve("acme acme/prod, sandbox/dev acme", "x", "dev", False, root)
    assert items == [{"tenant": "acme", "env": "dev"}, {"tenant": "acme", "env": "prod"}, {"tenant": "sandbox", "env": "dev"}]


def test_push_never_deploys_prod_and_missing_tenants_are_skipped(tmp_path):
    root = _repo(tmp_path)
    items, warnings = ci.resolve("acme/prod ghost sandbox/test bad_name acme/qa", "sandbox", "dev", True, root)
    assert items == []
    assert len(warnings) == 5 and any("prod is never" in w for w in warnings)


def test_real_repo_default_still_resolves():
    items, _ = ci.resolve("", "sandbox", "dev", True)
    assert items == [{"tenant": "sandbox", "env": "dev"}]


# ------------------------------------------------------------------ org-setup
def test_parse_remote():
    assert org.parse_remote("https://github.com/acme/connect-acc.git") == ("acme", "connect-acc")
    assert org.parse_remote("git@github.com:acme/connect-acc") == ("acme", "connect-acc")
    assert org.parse_remote("") is None


def test_copy_in_another_org_flags_serve7_default_tenant(tmp_path):
    root = _repo(tmp_path)
    rep = org.Report()
    ctx = org.check_repo(rep, root, remote="https://github.com/acme/acc.git", variables={})
    assert ctx["ci_tenant"] == "sandbox" and rep.blocking
    assert any("Serve7's account" in i["message"] for i in rep.items)


def test_default_tenant_variable_clears_the_blocker(tmp_path):
    root = _repo(tmp_path)
    rep = org.Report()
    org.check_repo(rep, root, remote="https://github.com/acme/acc.git", variables={"DEFAULT_TENANT": "acme"})
    assert not rep.blocking


def test_serve7_repo_is_not_blocked_and_drift_is_reported(tmp_path):
    root = _repo(tmp_path)
    (root / ".github" / "workflows" / "plan.yml").write_text("old")
    rep = org.Report()
    org.check_repo(rep, root, remote="https://github.com/serve7-io/amazon-connect-genai-accelerator", variables={})
    assert not rep.blocking
    assert any(i["area"] == "workflows" and "plan.yml" in i["message"] for i in rep.items)


def test_missing_remote_and_missing_ci_tenant_block(tmp_path):
    root = _repo(tmp_path)
    rep = org.Report()
    org.check_repo(rep, root, remote="", variables={"CI_TENANTS": "ghost/dev"})
    assert [i["area"] for i in rep.items if i["level"] == "todo"] == ["repo", "ci"]


def test_bootstrap_trust_issues():
    p = {"GitHubOrg": "serve7-io", "GitHubRepo": "amazon-connect-genai-accelerator", "DeployTrust": "environment",
         "GitHubSubjectPrefix": "repo:serve7-io@1/amazon-connect-genai-accelerator@2"}
    assert org.bootstrap_trust_issues(p, "serve7-io", "amazon-connect-genai-accelerator", True) == []
    issues = org.bootstrap_trust_issues(p, "acme", "acc", False)
    assert len(issues) == 3 and all("deploy.sh" in fix for _, fix in issues)
    p2 = dict(p, DeployTrust="main-branch", GitHubSubjectPrefix="")
    assert "USE_ENVIRONMENTS=false" in org.bootstrap_trust_issues(p2, "serve7-io", "amazon-connect-genai-accelerator", True)[0][1]


def test_github_commands():
    cmds = org.github_commands("acme/acc", "acme", "dev", environments=False, aws_ready=False)
    flat = [" ".join(c) for c in cmds]
    assert "gh variable set DEFAULT_TENANT --repo acme/acc --body acme" in flat
    assert "gh variable set USE_ENVIRONMENTS --repo acme/acc --body false" in flat
    assert "gh variable set AWS_CI --repo acme/acc --body false" in flat
    assert not any("environments/" in f for f in flat)
    assert any(f.endswith("environments/acme-dev") for f in
               (" ".join(c) for c in org.github_commands("acme/acc", "acme", "dev", True, True)))


def test_installed_workflow_sources_use_the_tenant_matrix():
    for name in ("deploy.yml", "plan.yml"):
        text = (ROOT / "ci" / "workflows" / name).read_text()
        assert "scripts/ci-tenants.py" in text and "fromJSON(needs.tenants.outputs.matrix)" in text
        assert "vars.AWS_CI != 'false'" in text
        assert "'sandbox'" not in text.replace("else sandbox", "")   # no hard-coded tenant in expressions
    assert "--no-prod" in (ROOT / "ci" / "workflows" / "deploy.yml").read_text()
