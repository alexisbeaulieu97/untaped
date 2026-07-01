"""Contract tests for the release smoke GitHub Actions workflow.

The release smoke proves the SDK *boundary*: the wheel builds, installs clean,
ships no central console script (the umbrella command is retired), and exposes
the exact public surface tools depend on. These tests pin that intent so the
workflow can't silently drift back into testing something else.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-smoke.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
WORKFLOWS = [CI_WORKFLOW, WORKFLOW, RELEASE_WORKFLOW]
FULL_SHA_ACTION_RE = re.compile(r"@[0-9a-f]{40}$")
EXPECTED_UV_VERSION = "0.11.26"
EXPECTED_ACTION_REFS = {
    "actions/cache": (
        "v6.1.0",
        "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
    ),
    "actions/checkout": (
        "v7.0.0",
        "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
    ),
    "actions/download-artifact": (
        "v8.0.1",
        "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    ),
    "actions/upload-artifact": (
        "v7.0.1",
        "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    ),
    "astral-sh/setup-uv": (
        "v8.2.0",
        "fac544c07dec837d0ccb6301d7b5580bf5edae39",
    ),
    "pypa/gh-action-pypi-publish": (
        "v1.14.0",
        "cef221092ed1bacb1cc03d23a2d87d1d172e277b",
    ),
}
SMOKE_JOB = "sdk-wheel-smoke"


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
    for step in workflow["jobs"][SMOKE_JOB]["steps"]:
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

    job = workflow["jobs"][SMOKE_JOB]
    assert job["runs-on"] == "ubuntu-latest"


def test_release_smoke_workflow_builds_and_installs_the_sdk_wheel() -> None:
    _, workflow = _load_workflow()

    assert "uv build --wheel" in _step_run(workflow, "Build the SDK wheel")

    install = _step_run(workflow, "Install the built wheel into an isolated venv")
    assert "uv venv" in install
    assert "uv pip install" in install
    assert "dist/*.whl" in install


def test_release_smoke_workflow_asserts_no_console_script() -> None:
    _, workflow = _load_workflow()
    run = _step_run(workflow, "Assert the SDK ships no console script")

    # The retired umbrella command must never reappear as an installed script.
    assert "bin/untaped" in run
    assert "exit 1" in run


def test_release_smoke_workflow_asserts_public_api_surface_resolves() -> None:
    _, workflow = _load_workflow()
    run = _step_run(workflow, "Assert the public API surface resolves")

    # Root re-exports api.py verbatim, and the composition contract is present.
    assert "untaped.__all__ == untaped.api.__all__" in run
    for name in ("ToolSpec", "run_tool", "app_context"):
        assert name in run


def test_workflow_actions_are_pinned_to_commit_shas() -> None:
    offenders: list[str] = []
    for path in WORKFLOWS:
        _, workflow = _load_yaml(path)
        for step in _steps(workflow):
            uses = step.get("uses")
            if uses and not FULL_SHA_ACTION_RE.search(uses):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {uses}")

    assert not offenders, "GitHub Action refs must be pinned to full SHAs:\n" + "\n".join(offenders)


def test_workflow_actions_use_latest_reviewed_release_shas() -> None:
    offenders: list[str] = []
    for path in WORKFLOWS:
        _, workflow = _load_yaml(path)
        for step in _steps(workflow):
            uses = step.get("uses")
            if not uses:
                continue
            action, ref = str(uses).rsplit("@", 1)
            expected = EXPECTED_ACTION_REFS.get(action)
            if expected is None:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: unreviewed action {action}")
                continue
            latest_tag, latest_sha = expected
            if ref != latest_sha:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}: {action}@{ref} "
                    f"does not match reviewed latest {latest_tag} ({latest_sha})"
                )

    assert not offenders, "GitHub Action pins are stale:\n" + "\n".join(offenders)


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
