"""Contract tests for the PyPI/TestPyPI release workflow."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"
BUILD_JOB = "build"
PUBLISH_JOB = "publish"
SMOKE_JOB = "smoke-published"
GITHUB_RELEASE_JOB = "github-release"
PYPA_PUBLISH_ACTION_SHA = "cef221092ed1bacb1cc03d23a2d87d1d172e277b"
UPLOAD_ARTIFACT_ACTION_SHA = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
DOWNLOAD_ARTIFACT_ACTION_SHA = "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"


def _load_release_workflow() -> tuple[str, dict[str, Any]]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def _job(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    return workflow["jobs"][name]


def _steps(workflow: dict[str, Any], job_name: str | None = None) -> list[dict[str, Any]]:
    if job_name is not None:
        return list(_job(workflow, job_name)["steps"])

    steps: list[dict[str, Any]] = []
    for job in workflow["jobs"].values():
        steps.extend(job["steps"])
    return steps


def _step(workflow: dict[str, Any], name: str, *, job_name: str | None = None) -> dict[str, Any]:
    for step in _steps(workflow, job_name):
        if step["name"] == name:
            return step
    raise AssertionError(f"workflow step not found: {name}")


def _all_run_text(workflow: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(workflow))


def test_release_workflow_dispatch_contract_and_permissions() -> None:
    _, workflow = _load_release_workflow()

    dispatch = workflow["on"]["workflow_dispatch"]["inputs"]
    assert dispatch["version"] == {
        "description": "Version to publish, without leading v.",
        "required": True,
        "type": "string",
    }
    assert dispatch["index"] == {
        "description": "Package index to publish to.",
        "required": True,
        "type": "choice",
        "options": ["testpypi", "pypi"],
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "${{ github.workflow }}-${{ inputs.index }}-${{ inputs.version }}",
        "cancel-in-progress": False,
    }

    assert set(workflow["jobs"]) == {BUILD_JOB, PUBLISH_JOB, SMOKE_JOB, GITHUB_RELEASE_JOB}


def test_release_workflow_uses_least_privilege_jobs() -> None:
    _, workflow = _load_release_workflow()

    build = _job(workflow, BUILD_JOB)
    assert build["permissions"] == {"contents": "read"}
    assert "environment" not in build

    publish = _job(workflow, PUBLISH_JOB)
    assert publish["needs"] == BUILD_JOB
    assert publish["environment"] == "${{ inputs.index }}"
    assert publish["permissions"] == {"contents": "read", "id-token": "write"}

    smoke = _job(workflow, SMOKE_JOB)
    assert smoke["needs"] == PUBLISH_JOB
    assert smoke["permissions"] == {"contents": "read"}
    assert "environment" not in smoke

    github_release = _job(workflow, GITHUB_RELEASE_JOB)
    assert github_release["needs"] == SMOKE_JOB
    assert github_release["if"] == "inputs.index == 'pypi'"
    assert github_release["permissions"] == {"contents": "write"}
    assert "id-token" not in github_release["permissions"]


def test_release_workflow_guards_production_publish_to_main() -> None:
    _, workflow = _load_release_workflow()

    step = _step(workflow, "Guard production publish", job_name=BUILD_JOB)
    assert step["if"] == "inputs.index == 'pypi'"
    run = str(step["run"])
    assert "refs/heads/main" in run
    assert "exit 1" in run


def test_release_workflow_validates_version_builds_without_sources_and_smokes_wheel() -> None:
    _, workflow = _load_release_workflow()
    run_text = _all_run_text(workflow)

    assert "uv sync --frozen --all-packages" in run_text
    assert "uv run pre-commit run --all-files --show-diff-on-failure" in run_text
    assert "uv run mypy" in run_text
    assert "uv run pytest" in run_text
    assert "pyproject.toml" in run_text
    assert "RELEASE_VERSION" in run_text
    assert "re.fullmatch" in run_text
    assert "uv build --no-sources" in run_text

    smoke = str(_step(workflow, "Smoke local wheel", job_name=BUILD_JOB)["run"])
    assert "uv venv" in smoke
    assert "uv pip install" in smoke
    assert "dist/*.whl" in smoke
    assert "untaped.__all__ == untaped.api.__all__" in smoke
    assert "bin/untaped" in smoke


def test_release_workflow_avoids_direct_input_interpolation_in_shell() -> None:
    _, workflow = _load_release_workflow()

    offenders = [
        step["name"]
        for step in _steps(workflow)
        if "${{ inputs.version }}" in str(step.get("run", ""))
    ]
    assert not offenders, "version inputs must be passed through env and validated first"


def test_release_workflow_hands_off_artifacts_between_build_and_publish_jobs() -> None:
    _, workflow = _load_release_workflow()

    upload = _step(workflow, "Upload package artifacts", job_name=BUILD_JOB)
    assert upload["uses"] == f"actions/upload-artifact@{UPLOAD_ARTIFACT_ACTION_SHA}"
    assert upload["with"] == {
        "name": "python-package-distributions",
        "path": "dist/*",
        "if-no-files-found": "error",
        "retention-days": 7,
    }

    download = _step(workflow, "Download package artifacts", job_name=PUBLISH_JOB)
    assert download["uses"] == f"actions/download-artifact@{DOWNLOAD_ARTIFACT_ACTION_SHA}"
    assert download["with"] == {
        "name": "python-package-distributions",
        "path": "dist/",
    }


def test_publish_job_only_downloads_and_publishes_artifacts() -> None:
    _, workflow = _load_release_workflow()

    publish_steps = _steps(workflow, PUBLISH_JOB)
    assert [step["name"] for step in publish_steps] == [
        "Download package artifacts",
        "Publish package to TestPyPI",
        "Publish package to PyPI",
    ]
    assert all("run" not in step for step in publish_steps)


def test_release_workflow_uses_trusted_publishing_action_with_attestations() -> None:
    _, workflow = _load_release_workflow()

    publish_steps = [
        step
        for step in _steps(workflow)
        if str(step.get("uses", "")).startswith("pypa/gh-action-pypi-publish@")
    ]
    assert publish_steps
    for step in publish_steps:
        assert step["uses"] == f"pypa/gh-action-pypi-publish@{PYPA_PUBLISH_ACTION_SHA}"
        assert "password" not in step.get("with", {})
        assert "user" not in step.get("with", {})

    testpypi = _step(workflow, "Publish package to TestPyPI", job_name=PUBLISH_JOB)
    assert testpypi["if"] == "inputs.index == 'testpypi'"
    assert testpypi["with"]["repository-url"] == "https://test.pypi.org/legacy/"
    assert testpypi["with"]["attestations"] is True

    pypi = _step(workflow, "Publish package to PyPI", job_name=PUBLISH_JOB)
    assert pypi["if"] == "inputs.index == 'pypi'"
    assert "repository-url" not in pypi.get("with", {})
    assert pypi["with"]["attestations"] is True


def test_release_workflow_smokes_published_package_from_selected_index() -> None:
    _, workflow = _load_release_workflow()

    smoke = str(_step(workflow, "Smoke published package", job_name=SMOKE_JOB)["run"])
    assert "UV_INDEX=https://test.pypi.org/simple/" in smoke
    assert "UV_INDEX_STRATEGY=unsafe-best-match" in smoke
    assert "uv pip install" in smoke
    assert "untaped==$RELEASE_VERSION" in smoke
    assert "untaped.__all__ == untaped.api.__all__" in smoke
    assert "bin/untaped" in smoke


def test_release_workflow_creates_github_release_only_after_production_smoke() -> None:
    _, workflow = _load_release_workflow()

    release = _step(workflow, "Create GitHub release", job_name=GITHUB_RELEASE_JOB)
    assert "gh release create" in str(release["run"])
    assert "v$RELEASE_VERSION" in str(release["run"])


def test_release_workflow_reports_burn_recovery_after_upload_failures() -> None:
    _, workflow = _load_release_workflow()

    run_text = _all_run_text(workflow).lower()
    assert "version may be burned" in run_text
    assert "bump patch" in run_text


def test_project_metadata_declares_pypi_release_fields() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]

    assert project["version"] == "2.4.4"
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project.get("readme") == "README.md"
    assert not any(str(item).startswith("License ::") for item in project.get("classifiers", []))
