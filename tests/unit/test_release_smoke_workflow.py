"""Contract tests for the release smoke and the supply-chain hygiene of every
GitHub Actions workflow.

The release smoke proves the unified application boundary: the wheel builds,
installs clean, exposes the executable, and resolves every root and built-in
capability command through the shared checked-in smoke helper.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOWS = [WORKFLOW_DIR / name for name in ("ci.yml", "release-smoke.yml", "release.yml")]
EXPECTED_UV_VERSION = "0.11.26"
# action -> (reviewed release tag, the full commit SHA it must be pinned to)
EXPECTED_ACTION_REFS = {
    "actions/cache": ("v6.1.0", "55cc8345863c7cc4c66a329aec7e433d2d1c52a9"),
    "actions/checkout": ("v7.0.0", "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"),
    "actions/download-artifact": ("v8.0.1", "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"),
    "actions/upload-artifact": ("v7.0.1", "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"),
    "astral-sh/setup-uv": ("v8.2.0", "fac544c07dec837d0ccb6301d7b5580bf5edae39"),
    "pypa/gh-action-pypi-publish": ("v1.14.0", "cef221092ed1bacb1cc03d23a2d87d1d172e277b"),
}


def _steps(path: Path) -> list[dict[str, Any]]:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


def test_release_smoke_runs_on_pr_and_main_and_uses_the_shared_smoke() -> None:
    path = WORKFLOW_DIR / "release-smoke.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert workflow["on"] == {
        "pull_request": None,
        "push": {"branches": ["main"]},
        "workflow_dispatch": None,
    }
    assert workflow["permissions"] == {"contents": "read"}

    run = "\n".join(str(step.get("run", "")) for step in _steps(path))
    assert "uv build --wheel" in run
    assert "dist/*.whl" in run
    assert "release.py smoke-unified" in run
    assert "--console-script untaped" in run


def test_workflow_actions_are_pinned_to_reviewed_release_shas() -> None:
    offenders: list[str] = []
    for path in WORKFLOWS:
        for step in _steps(path):
            uses = step.get("uses")
            if not uses:
                continue
            action, ref = str(uses).rsplit("@", 1)
            expected = EXPECTED_ACTION_REFS.get(action)
            if expected is None:
                offenders.append(f"{path.name}: unreviewed action {action}")
            elif ref != expected[1]:
                offenders.append(
                    f"{path.name}: {action}@{ref} is not {expected[0]} ({expected[1]})"
                )

    assert not offenders, "GitHub Action pins are stale or unpinned:\n" + "\n".join(offenders)


def test_checkout_and_setup_uv_steps_are_hardened() -> None:
    offenders: list[str] = []
    for path in WORKFLOWS:
        for step in _steps(path):
            uses = str(step.get("uses", ""))
            options = step.get("with", {})
            if (
                uses.startswith("actions/checkout@")
                and options.get("persist-credentials") is not False
            ):
                offenders.append(f"{path.name}: checkout must set persist-credentials: false")
            if (
                uses.startswith("astral-sh/setup-uv@")
                and options.get("version") != EXPECTED_UV_VERSION
            ):
                offenders.append(f"{path.name}: setup-uv must pin uv {EXPECTED_UV_VERSION}")

    assert not offenders, "\n".join(offenders)
