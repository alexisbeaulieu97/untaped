"""Contract tests for the reusable PyPI/TestPyPI package release workflow."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pypi-package-release.yml"

BUILD_JOB = "build"
PUBLISH_JOB = "publish"
SMOKE_JOB = "smoke-published"
GITHUB_RELEASE_JOB = "github-release"
EXPECTED_UV_VERSION = "0.11.26"
EXPECTED_ACTION_REFS = {
    "actions/checkout": "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
    "actions/cache": "55cc8345863c7cc4c66a329aec7e433d2d1c52a9",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "astral-sh/setup-uv": "fac544c07dec837d0ccb6301d7b5580bf5edae39",
    "pypa/gh-action-pypi-publish": "cef221092ed1bacb1cc03d23a2d87d1d172e277b",
}
USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s+([^\s#]+)(?:\s+#.*)?\s*$", re.MULTILINE)
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(_workflow_text())


def _ci_workflow() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _workflow_steps(job_name: str) -> list[dict[str, Any]]:
    return list(_workflow()["jobs"][job_name]["steps"])


def _workflow_step(job_name: str, name: str) -> dict[str, Any]:
    for step in _workflow_steps(job_name):
        if step["name"] == name:
            return step
    raise AssertionError(f"workflow step not found in {job_name}: {name}")


def _workflow_steps_by_job() -> list[tuple[str, dict[str, Any]]]:
    return [
        (job_name, step) for job_name in _workflow()["jobs"] for step in _workflow_steps(job_name)
    ]


def _workflow_step_names(job_name: str) -> list[str]:
    return [step["name"] for step in _workflow_steps(job_name)]


def _is_action(step: dict[str, Any], action: str) -> bool:
    return str(step.get("uses", "")).startswith(f"{action}@")


def _step_block(name: str) -> str:
    text = _workflow_text()
    next_step_or_job = r"(?=^      - name: |^  [a-zA-Z0-9_-]+:|\Z)"
    pattern = rf"(?ms)^      - name: {re.escape(name)}\n.*?{next_step_or_job}"
    match = re.search(pattern, text)
    assert match is not None, f"workflow step not found: {name}"
    return match.group(0)


def _unpinned_action_refs(text: str) -> list[str]:
    offenders: list[str] = []
    for action_ref in USES_RE.findall(text):
        if "@" not in action_ref:
            offenders.append(action_ref)
            continue
        _, ref = action_ref.rsplit("@", maxsplit=1)
        if not FULL_SHA_RE.fullmatch(ref):
            offenders.append(action_ref)
    return offenders


def test_reusable_workflow_call_inputs_concurrency_and_jobs() -> None:
    workflow = _workflow()

    call = workflow["on"]["workflow_call"]["inputs"]
    assert call == {
        "version": {"required": True, "type": "string"},
        "index": {"required": True, "type": "string"},
        "package": {"required": True, "type": "string"},
        "console-script": {"required": True, "type": "string"},
        "python-version": {"required": False, "type": "string", "default": "3.14"},
        "release-tool-ref": {"required": True, "type": "string"},
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "${{ github.workflow }}-${{ inputs.index }}-${{ inputs.version }}",
        "cancel-in-progress": False,
    }
    assert set(workflow["jobs"]) == {BUILD_JOB, PUBLISH_JOB, SMOKE_JOB, GITHUB_RELEASE_JOB}


def test_reusable_workflow_uses_least_privilege_jobs() -> None:
    jobs = _workflow()["jobs"]

    assert jobs[BUILD_JOB]["permissions"] == {"contents": "read"}
    assert "id-token" not in jobs[BUILD_JOB]["permissions"]
    assert "environment" not in jobs[BUILD_JOB]

    assert jobs[PUBLISH_JOB]["needs"] == BUILD_JOB
    assert jobs[PUBLISH_JOB]["environment"] == "${{ inputs.index }}"
    assert jobs[PUBLISH_JOB]["permissions"] == {"contents": "read", "id-token": "write"}

    assert jobs[SMOKE_JOB]["needs"] == PUBLISH_JOB
    assert jobs[SMOKE_JOB]["permissions"] == {"contents": "read"}
    assert "id-token" not in jobs[SMOKE_JOB]["permissions"]
    assert "environment" not in jobs[SMOKE_JOB]

    assert jobs[GITHUB_RELEASE_JOB]["needs"] == SMOKE_JOB
    assert jobs[GITHUB_RELEASE_JOB]["if"] == "inputs.index == 'pypi'"
    assert jobs[GITHUB_RELEASE_JOB]["permissions"] == {"contents": "write"}
    assert "id-token" not in jobs[GITHUB_RELEASE_JOB]["permissions"]
    assert "environment" not in jobs[GITHUB_RELEASE_JOB]


def test_reusable_workflow_uses_latest_reviewed_action_shas() -> None:
    text = _workflow_text()
    unpinned = _unpinned_action_refs(text)
    assert not unpinned, "release workflow actions must be pinned to full SHAs:\n" + "\n".join(
        unpinned
    )

    offenders: list[str] = []
    refs = USES_RE.findall(text)
    assert refs, "reusable workflow must use pinned actions"
    for action_ref in refs:
        action, ref = action_ref.rsplit("@", maxsplit=1)
        expected = EXPECTED_ACTION_REFS.get(action)
        if expected is None:
            offenders.append(f"unreviewed action {action}")
        elif ref != expected:
            offenders.append(f"{action}@{ref} does not match reviewed SHA {expected}")

    assert not offenders, "GitHub Action pins are stale:\n" + "\n".join(offenders)


def test_checkout_steps_do_not_persist_credentials_and_include_release_tool_checkout() -> None:
    for job_name in (BUILD_JOB, SMOKE_JOB):
        checkouts = [
            step for step in _workflow_steps(job_name) if _is_action(step, "actions/checkout")
        ]
        assert len(checkouts) == 2
        for checkout in checkouts:
            assert checkout["with"]["persist-credentials"] is False

        release_tool = next(
            step
            for step in checkouts
            if step["with"].get("repository") == "alexisbeaulieu97/untaped"
        )
        assert release_tool["with"]["ref"] == "${{ inputs.release-tool-ref }}"
        assert release_tool["with"]["path"] == ".release-tool"


def test_setup_uv_steps_pin_version_and_expected_cache_settings() -> None:
    setup_steps = [
        (job_name, step)
        for job_name, step in _workflow_steps_by_job()
        if _is_action(step, "astral-sh/setup-uv")
    ]

    assert setup_steps, "reusable workflow must contain astral-sh/setup-uv steps"
    version_offenders = [
        f"{job_name}:{step['name']}"
        for job_name, step in setup_steps
        if step["with"]["version"] != EXPECTED_UV_VERSION
    ]
    assert not version_offenders, (
        f"setup-uv steps must pin uv {EXPECTED_UV_VERSION}:\n" + "\n".join(version_offenders)
    )

    cache_expectations = {
        (BUILD_JOB, "Install uv"): True,
        (SMOKE_JOB, "Install uv"): True,
    }
    by_job_and_name = {(job_name, step["name"]): step for job_name, step in setup_steps}
    for key, expected_cache in cache_expectations.items():
        assert by_job_and_name[key]["with"]["enable-cache"] is expected_cache


def test_reusable_workflow_keeps_anti_burn_guards_before_project_sync() -> None:
    build_steps = _workflow_step_names(BUILD_JOB)
    text = _workflow_text()

    production_guard = _step_block("Guard production publish")
    assert "if: inputs.index == 'pypi'" in production_guard
    assert "refs/heads/main" in production_guard
    assert "exit 1" in production_guard

    version_guard = _step_block("Verify release version")
    assert ".release-tool/.github/release/release.py verify-version" in version_guard
    assert '--pyproject "$GITHUB_WORKSPACE/pyproject.toml"' in version_guard

    unused_guard = _step_block("Verify production release target is unused")
    assert "if: inputs.index == 'pypi'" in unused_guard
    assert ".release-tool/.github/release/release.py verify-target-unused" in unused_guard

    dependency_guard = _step_block("Verify internal dependencies resolve from selected index")
    assert (
        ".release-tool/.github/release/release.py verify-internal-dependencies-published"
        in dependency_guard
    )
    assert '--pyproject "$GITHUB_WORKSPACE/pyproject.toml"' in dependency_guard

    sync_index = build_steps.index("Sync project")
    assert build_steps.index("Verify release version") < sync_index
    assert build_steps.index("Verify production release target is unused") < sync_index
    assert (
        build_steps.index("Verify internal dependencies resolve from selected index") < sync_index
    )
    assert "version may be burned" not in dependency_guard.lower()
    assert "uv publish" not in text


def test_reusable_workflow_maps_inputs_through_env_before_shell_use() -> None:
    workflow = _workflow()
    expected_env = {
        "RELEASE_INDEX": "${{ inputs.index }}",
        "RELEASE_VERSION": "${{ inputs.version }}",
        "RELEASE_PACKAGE": "${{ inputs.package }}",
        "RELEASE_CONSOLE_SCRIPT": "${{ inputs.console-script }}",
        "RELEASE_PYTHON_VERSION": "${{ inputs.python-version }}",
    }
    for job_name in (BUILD_JOB, SMOKE_JOB):
        assert workflow["jobs"][job_name]["env"] == expected_env

    release_env = workflow["jobs"][GITHUB_RELEASE_JOB]["env"]
    assert release_env["RELEASE_VERSION"] == "${{ inputs.version }}"
    assert release_env["RELEASE_PACKAGE"] == "${{ inputs.package }}"

    offenders = [
        f"{job_name}:{step['name']}"
        for job_name, step in _workflow_steps_by_job()
        if re.search(r"\${{\s*inputs\.", str(step.get("run", "")))
    ]
    assert not offenders, "workflow inputs must be passed through env before shell use"


def test_reusable_workflow_builds_without_sources_and_smokes_local_tool_wheel() -> None:
    text = _workflow_text()

    assert "uv sync --frozen" in text
    assert "uv run pre-commit run --all-files --show-diff-on-failure" in text
    assert "uv run mypy" in text
    assert "uv run pytest" in text
    assert "uv build --no-sources" in text

    smoke = _step_block("Smoke local wheel")
    assert 'uv venv --python "$RELEASE_PYTHON_VERSION"' in smoke
    assert "uv pip install" in smoke
    assert "dist/*.whl" in smoke
    assert ".release-tool/.github/release/release.py smoke-console" in smoke
    assert '--package "$RELEASE_PACKAGE"' in smoke
    assert '--version "$RELEASE_VERSION"' in smoke
    assert '--venv "$RUNNER_TEMP/local-wheel"' in smoke
    assert '--console-script "$RELEASE_CONSOLE_SCRIPT"' in smoke


def test_reusable_workflow_hands_artifacts_to_trusted_publisher() -> None:
    workflow = _workflow()
    text = _workflow_text()

    upload = _workflow_step(BUILD_JOB, "Upload package artifacts")
    assert (
        upload["uses"]
        == f"actions/upload-artifact@{EXPECTED_ACTION_REFS['actions/upload-artifact']}"
    )
    assert upload["with"] == {
        "name": "python-package-distributions",
        "path": "dist/*",
        "if-no-files-found": "error",
        "retention-days": 7,
    }

    download = _workflow_step(PUBLISH_JOB, "Download package artifacts")
    assert download["uses"] == (
        f"actions/download-artifact@{EXPECTED_ACTION_REFS['actions/download-artifact']}"
    )
    assert download["with"] == {
        "name": "python-package-distributions",
        "path": "dist/",
    }

    publish_steps = [
        step
        for step in _workflow_steps(PUBLISH_JOB)
        if _is_action(step, "pypa/gh-action-pypi-publish")
    ]
    assert publish_steps
    for step in publish_steps:
        assert step["uses"] == (
            f"pypa/gh-action-pypi-publish@{EXPECTED_ACTION_REFS['pypa/gh-action-pypi-publish']}"
        )
        assert step["with"]["attestations"] is True
        assert "password" not in step.get("with", {})
        assert "user" not in step.get("with", {})

    testpypi = _workflow_step(PUBLISH_JOB, "Publish package to TestPyPI")
    assert testpypi["if"] == "inputs.index == 'testpypi'"
    assert testpypi["with"]["repository-url"] == "https://test.pypi.org/legacy/"

    pypi = _workflow_step(PUBLISH_JOB, "Publish package to PyPI")
    assert pypi["if"] == "inputs.index == 'pypi'"
    assert "repository-url" not in pypi.get("with", {})
    assert "uv publish" not in text
    assert workflow["jobs"][PUBLISH_JOB]["permissions"] == {"contents": "read", "id-token": "write"}


def test_reusable_workflow_smokes_published_tool_from_selected_index() -> None:
    smoke = _step_block("Smoke published package")

    assert "UV_INDEX=https://test.pypi.org/simple/" in smoke
    assert "UV_INDEX_STRATEGY=unsafe-best-match" in smoke
    assert 'uv venv --python "$RELEASE_PYTHON_VERSION" "$published_venv"' in smoke
    assert smoke.index(
        'uv venv --python "$RELEASE_PYTHON_VERSION" "$published_venv"'
    ) < smoke.index("for attempt in")
    assert 'rm -rf "$published_venv"' not in smoke
    assert '--refresh-package "$RELEASE_PACKAGE"' in smoke
    assert '"$RELEASE_PACKAGE==$RELEASE_VERSION"' in smoke
    assert ".release-tool/.github/release/release.py smoke-console" in smoke
    assert '--package "$RELEASE_PACKAGE"' in smoke
    assert '--console-script "$RELEASE_CONSOLE_SCRIPT"' in smoke
    assert '--venv "$published_venv"' in smoke
    assert "version may be burned" in smoke.lower()
    assert "bump patch" in smoke.lower()


def test_reusable_workflow_creates_github_release_only_after_pypi_smoke() -> None:
    release = _step_block("Create GitHub release")

    assert _workflow()["jobs"][GITHUB_RELEASE_JOB]["needs"] == SMOKE_JOB
    assert "gh release create" in release
    assert ' --repo "$GITHUB_REPOSITORY"' in release
    assert ' --target "$GITHUB_SHA"' in release
    assert "v$RELEASE_VERSION" in release
    assert "$RELEASE_PACKAGE v$RELEASE_VERSION" in release


def test_ci_runs_shared_release_helper_contracts_before_project_sync() -> None:
    workflow = _ci_workflow()
    steps = workflow["jobs"]["lint-and-test"]["steps"]
    names = [step["name"] for step in steps]

    assert names.index("Shared release helper tests") < names.index("Sync workspace")
    run = str(next(step for step in steps if step["name"] == "Shared release helper tests")["run"])
    assert "python3 .github/release/release.py verify-version --help" in run
    assert "python3 .github/release/release.py verify-internal-dependencies-published --help" in run
    assert "uv run --no-project --with pytest python -m pytest .github/release/tests -q" in run
    assert "-o addopts=" in run
