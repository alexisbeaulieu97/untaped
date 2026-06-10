"""Contract tests for the release smoke GitHub Actions workflow."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-smoke.yml"
WORKFLOWS = [CI_WORKFLOW, WORKFLOW]
FULL_SHA_ACTION_RE = re.compile(r"@[0-9a-f]{40}$")
EXPECTED_UV_VERSION = "0.11.19"

EXPECTED_PLUGIN_NAMES = [
    "ansible",
    "awx",
    "github",
    "jira",
    "profile",
    "themes",
    "workspace",
]

EXPECTED_SKILL_NAMES = [
    "untaped",
    "untaped-ansible",
    "untaped-awx",
    "untaped-github",
    "untaped-jira",
    "untaped-profile",
    "untaped-workspace",
]

EXPECTED_PLUGIN_SPECS = [
    "untaped-awx @ git+https://github.com/alexisbeaulieu97/untaped-awx@cyclopts-migration",
    "untaped-workspace @ git+https://github.com/alexisbeaulieu97/untaped-workspace@cyclopts-migration",
    "untaped-github @ git+https://github.com/alexisbeaulieu97/untaped-github@cyclopts-migration",
    "untaped-ansible @ git+https://github.com/alexisbeaulieu97/untaped-ansible@cyclopts-migration",
    "untaped-jira @ git+https://github.com/alexisbeaulieu97/untaped-jira@cyclopts-migration",
    "untaped-profile @ git+https://github.com/alexisbeaulieu97/untaped-profile@cyclopts-migration",
    "untaped-themes @ git+https://github.com/alexisbeaulieu97/untaped-themes@cyclopts-migration",
]


def _load_yaml(path: Path) -> tuple[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def _load_workflow() -> tuple[str, dict[str, Any]]:
    return _load_yaml(WORKFLOW)


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for job in workflow["jobs"].values():
        steps.extend(job["steps"])
    return steps


def _step_run(workflow: dict[str, Any], name: str) -> str:
    for step in workflow["jobs"]["release-smoke"]["steps"]:
        if step["name"] == name:
            return str(step["run"])
    raise AssertionError(f"workflow step not found: {name}")


def test_release_smoke_workflow_runs_on_pr_main_push_and_manual_dispatch() -> None:
    _, workflow = _load_workflow()

    assert workflow["on"] == {
        "pull_request": None,
        "push": {"branches": ["main"]},
        "workflow_dispatch": None,
    }
    assert workflow["permissions"] == {"contents": "read"}

    job = workflow["jobs"]["release-smoke"]
    assert job["runs-on"] == "ubuntu-latest"


def test_release_smoke_workflow_exports_isolated_temp_paths() -> None:
    _, workflow = _load_workflow()
    run = _step_run(workflow, "Configure temp paths")

    assert 'echo "HOME=$RUNNER_TEMP/home"' in run
    assert 'echo "XDG_DATA_HOME=$RUNNER_TEMP/data"' in run
    assert 'echo "UNTAPED_CONFIG=$RUNNER_TEMP/untaped-config.yml"' in run
    assert 'echo "$RUNNER_TEMP/home/.local/bin" >> "$GITHUB_PATH"' in run


def test_workflow_actions_are_pinned_to_commit_shas() -> None:
    offenders: list[str] = []
    for path in WORKFLOWS:
        _, workflow = _load_yaml(path)
        for step in _steps(workflow):
            uses = step.get("uses")
            if uses and not FULL_SHA_ACTION_RE.search(uses):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {uses}")

    assert not offenders, "GitHub Action refs must be pinned to full SHAs:\n" + "\n".join(offenders)


def test_checkout_steps_do_not_persist_credentials() -> None:
    offenders: list[str] = []
    for path in WORKFLOWS:
        _, workflow = _load_yaml(path)
        for step in _steps(workflow):
            uses = step.get("uses", "")
            if (
                uses.startswith("actions/checkout@")
                and step.get("with", {}).get("persist-credentials") is not False
            ):
                offenders.append(str(path.relative_to(REPO_ROOT)))

    assert not offenders, "checkout steps must set persist-credentials: false"


def test_setup_uv_steps_pin_uv_version() -> None:
    offenders: list[str] = []
    for path in WORKFLOWS:
        _, workflow = _load_yaml(path)
        for step in _steps(workflow):
            uses = step.get("uses", "")
            if (
                uses.startswith("astral-sh/setup-uv@")
                and step.get("with", {}).get("version") != EXPECTED_UV_VERSION
            ):
                offenders.append(str(path.relative_to(REPO_ROOT)))

    assert not offenders, f"setup-uv steps must pin uv {EXPECTED_UV_VERSION}:\n" + "\n".join(
        offenders
    )


def test_release_smoke_workflow_installs_current_core_with_migration_plugin_stack() -> None:
    text, _ = _load_workflow()

    assert "Install current core with migration plugin stack" in text
    assert "scripts/install.sh --editable ." in text
    assert "uv tool install" not in text
    assert "--no-sources" not in text
    for spec in EXPECTED_PLUGIN_SPECS:
        assert spec in text


def test_release_smoke_workflow_pins_plugin_and_skill_discovery_contracts() -> None:
    _, workflow = _load_workflow()
    plugin_run = _step_run(workflow, "Verify plugin discovery")
    skill_run = _step_run(workflow, "Verify skill discovery")

    assert "untaped plugins list --format raw --columns plugin_id | sort" in plugin_run
    assert "untaped skills list --format raw --columns name" in skill_run
    assert "\n".join(EXPECTED_PLUGIN_NAMES) in plugin_run
    assert "\n".join(EXPECTED_SKILL_NAMES) in skill_run


def test_release_smoke_workflow_verifies_local_agent_skill_installation() -> None:
    _, workflow = _load_workflow()
    run = _step_run(workflow, "Verify local agent skill installation")

    assert (
        'untaped skills install --all --target all --scope local --project-dir "$project_dir"'
        in run
    )
    for agent_dir in [".agents", ".claude"]:
        assert f'"$project_dir/{agent_dir}/skills/$skill/SKILL.md"' in run
        assert f'"$project_dir/{agent_dir}/skills/$skill/.untaped-skill.json"' in run
