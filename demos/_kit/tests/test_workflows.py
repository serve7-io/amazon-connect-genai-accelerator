"""Static checks on the kit's GitHub workflows (source copies in demos/_kit/*.workflow.yml)."""
from pathlib import Path

import pytest
import yaml

KIT = Path(__file__).resolve().parents[1]
REPO = KIT.parents[1]
AUTO = yaml.safe_load((KIT / "auto-ship.workflow.yml").read_text(encoding="utf-8"))
DEMO = yaml.safe_load((KIT / "demo.workflow.yml").read_text(encoding="utf-8"))


def steps():
    return AUTO["jobs"]["ship"]["steps"]


def step(sid=None, name=None):
    return next(s for s in steps() if (sid and s.get("id") == sid) or (name and s.get("name") == name))


def test_auto_ship_triggers_only_on_auto_branches():
    on = AUTO[True]                     # YAML 1.1 reads the key "on" as True
    assert on == {"push": {"branches": ["auto/**"]}}


def test_auto_ship_owner_gate_runs_first():
    assert AUTO["env"]["OWNERS"] == "milosjovanic-serve7"
    first = steps()[0]
    assert "$OWNERS" in first["run"] and "exit 1" in first["run"] and first["env"]["ACTOR"] == "${{ github.actor }}"


def test_auto_ship_permissions_and_concurrency():
    assert AUTO["permissions"] == {"contents": "write", "pull-requests": "write", "actions": "write"}
    assert AUTO["concurrency"]["cancel-in-progress"] is False


def test_auto_ship_runs_the_script_then_deploys_only_when_shipped():
    assert "auto_ship.py ship" in step(sid="ship")["run"]
    deploy = step(name="Deploy the changed demos")
    assert "steps.ship.outputs.shipped == 'true'" in deploy["if"]
    assert "gh workflow run demo.yml --ref main" in deploy["run"]


def test_auto_ship_pull_request_step_is_guarded_and_tolerant():
    pr = step(name="Open a pull request for review")
    assert "steps.ship.conclusion != 'skipped'" in pr["if"]
    assert "could not open a pull request" in pr["run"]


def test_demo_workflow_lets_the_bot_dispatch_deploys():
    assert DEMO["env"]["ALLOWED_ACTORS"].split() == ["milosjovanic-serve7", "github-actions[bot]"]


@pytest.mark.parametrize("name", ["demo", "auto-ship"])
def test_live_workflows_match_their_source_copies(name):
    live = REPO / ".github" / "workflows" / f"{name}.yml"
    if not live.is_file():
        pytest.skip(f"{live.name} is not live yet")
    assert live.read_text(encoding="utf-8") == (KIT / f"{name}.workflow.yml").read_text(encoding="utf-8")
