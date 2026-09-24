"""Contract tests for the PyPI/TestPyPI release workflow.

Only release-safety properties are pinned here (inputs, privileges, the
main-branch guard, version/candidate verification, trusted publishing and
ordering), not step names or incidental shell.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"
RELEASE_MANIFEST = REPO_ROOT / "release-manifest.toml"
BUILD_JOB = "build"
DRAFT_JOB = "github-draft"
PUBLISH_JOB = "publish"
SMOKE_JOB = "smoke-published"
GITHUB_RELEASE_JOB = "github-release"


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(workflow: dict[str, Any], job_name: str | None = None) -> list[dict[str, Any]]:
    jobs = [workflow["jobs"][job_name]] if job_name else workflow["jobs"].values()
    return [step for job in jobs for step in job["steps"]]


def _step(workflow: dict[str, Any], name: str, *, job_name: str) -> dict[str, Any]:
    for step in _steps(workflow, job_name):
        if step["name"] == name:
            return step
    raise AssertionError(f"workflow step not found: {name}")


def _run_text(workflow: dict[str, Any], job_name: str | None = None) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(workflow, job_name))


def test_release_workflow_dispatch_contract_and_permissions() -> None:
    workflow = _workflow()

    dispatch = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(dispatch) == {"version", "index", "candidate_oid"}
    assert all(field["required"] for field in dispatch.values())
    assert dispatch["index"]["options"] == ["testpypi", "pypi"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert set(workflow["jobs"]) == {
        BUILD_JOB,
        DRAFT_JOB,
        PUBLISH_JOB,
        SMOKE_JOB,
        GITHUB_RELEASE_JOB,
    }


def test_release_workflow_uses_least_privilege_jobs() -> None:
    jobs = _workflow()["jobs"]

    assert jobs[BUILD_JOB]["permissions"] == {"contents": "read"}
    assert "environment" not in jobs[BUILD_JOB]

    publish = jobs[PUBLISH_JOB]
    assert publish["needs"] == [BUILD_JOB, DRAFT_JOB]
    assert publish["environment"] == "${{ inputs.index }}"
    assert publish["permissions"] == {"contents": "read", "id-token": "write"}

    assert jobs[SMOKE_JOB]["needs"] == PUBLISH_JOB
    assert jobs[SMOKE_JOB]["permissions"] == {"contents": "read"}

    # The GitHub release is cut only for PyPI, only after the published smoke.
    github_release = jobs[GITHUB_RELEASE_JOB]
    assert github_release["needs"] == [SMOKE_JOB, DRAFT_JOB]
    assert "inputs.index == 'pypi'" in github_release["if"]
    assert "needs.smoke-published.result == 'success'" in github_release["if"]
    assert github_release["permissions"] == {"contents": "write"}

    assert jobs[DRAFT_JOB]["if"] == "inputs.index == 'pypi'"
    assert jobs[DRAFT_JOB]["permissions"] == {"contents": "write"}


def test_release_workflow_guards_production_publish_to_main() -> None:
    step = _step(_workflow(), "Guard production publish", job_name=BUILD_JOB)
    assert step["if"] == "inputs.index == 'pypi'"
    assert "refs/heads/main" in str(step["run"])
    assert "exit 1" in str(step["run"])


def test_release_workflow_verifies_version_and_candidate_before_building() -> None:
    build = _run_text(_workflow(), BUILD_JOB)
    for needle in (
        "uv sync --locked --all-packages",
        "uv run mypy",
        "uv run pytest",
        "verify-version",
        "verify-candidate",
        "uv build --no-sources",
        "smoke-unified",
    ):
        assert needle in build


def test_release_workflow_avoids_direct_input_interpolation_in_shell() -> None:
    offenders = [
        step["name"]
        for step in _steps(_workflow())
        if "${{ inputs.version }}" in str(step.get("run", ""))
    ]
    assert not offenders, "version inputs must be passed through env and validated first"


def test_release_workflow_uses_trusted_publishing_with_attestations() -> None:
    workflow = _workflow()
    publish_steps = [
        step
        for step in _steps(workflow)
        if str(step.get("uses", "")).startswith("pypa/gh-action-pypi-publish@")
    ]
    assert len(publish_steps) == 2
    for step in publish_steps:
        assert "password" not in step["with"]
        assert "user" not in step["with"]
        assert step["with"]["attestations"] is True

    testpypi = _step(workflow, "Publish package to TestPyPI", job_name=PUBLISH_JOB)
    assert testpypi["with"]["repository-url"] == "https://test.pypi.org/legacy/"
    pypi = _step(workflow, "Publish package to PyPI", job_name=PUBLISH_JOB)
    assert "repository-url" not in pypi["with"]


def test_release_workflow_smokes_published_package_from_selected_index() -> None:
    smoke = _run_text(_workflow(), SMOKE_JOB)
    assert "UV_INDEX=https://test.pypi.org/simple/" in smoke
    assert "untaped==$RELEASE_VERSION" in smoke
    assert "smoke-unified" in smoke
    assert "verify-index-artifacts" in smoke


def test_project_metadata_declares_pypi_release_fields() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    manifest = tomllib.loads(RELEASE_MANIFEST.read_text(encoding="utf-8"))["manifest"]

    assert project["version"] == manifest["version"]
    assert manifest["capabilities"] == ["workspace", "github", "jira", "awx", "ansible", "recipe"]
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project.get("readme") == "README.md"
    assert not any(str(item).startswith("License ::") for item in project.get("classifiers", []))
