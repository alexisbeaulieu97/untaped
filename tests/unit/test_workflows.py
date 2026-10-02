"""Contract tests for the GitHub Actions workflows.

They pin the tag-triggered release's routing (only a pushed tag reaches PyPI),
its least-privilege jobs and step order, CI's use of ``scripts/release.py``,
and the supply-chain hygiene of every workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOWS = [WORKFLOW_DIR / name for name in ("ci.yml", "release.yml")]
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
PRODUCTION = "github.event_name == 'push' && github.ref_type == 'tag'"
INDEX_ROUTE = "${{ " + PRODUCTION + " && 'pypi' || 'testpypi' }}"


def _load(name: str) -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))
    return workflow


def _steps(path: Path) -> list[dict[str, Any]]:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


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


def test_every_routing_point_uses_the_production_expression() -> None:
    """Only a pushed tag is production; everything else is a TestPyPI rehearsal.

    Truth table: push/tag -> PyPI + GitHub release; workflow_dispatch/tag,
    workflow_dispatch/branch and push/branch -> TestPyPI, no GitHub release.
    A dispatch that targets a tag is a rehearsal, so the expression must check
    the event as well as the ref type, and every routing point repeats it.
    """
    workflow = _load("release.yml")
    jobs = workflow["jobs"]
    assert workflow["env"]["PRODUCTION"] == "${{ " + PRODUCTION + " }}"
    assert jobs["publish"]["environment"] == INDEX_ROUTE
    assert jobs["verify"]["env"]["INDEX"] == INDEX_ROUTE
    assert jobs["github-release"]["if"] == PRODUCTION
    index_steps = [s for s in jobs["build"]["steps"] if "release.py index" in str(s.get("run"))]
    assert [s["env"]["INDEX"] for s in index_steps] == [
        "${{ env.PRODUCTION == 'true' && 'pypi' || 'testpypi' }}"
    ]
    publish_ifs = {
        s["name"]: s["if"]
        for s in jobs["publish"]["steps"]
        if "pypi-publish" in str(s.get("uses", ""))
    }
    assert publish_ifs == {
        "Publish to PyPI": "env.PRODUCTION == 'true'",
        "Publish to TestPyPI": "env.PRODUCTION != 'true'",
    }


def test_triggers_and_default_permissions() -> None:
    workflow = _load("release.yml")
    assert workflow["on"] == {"push": {"tags": ["v*"]}, "workflow_dispatch": {}}
    assert workflow["permissions"] == {"contents": "read"}


def test_only_publish_mints_tokens_and_only_the_release_job_writes() -> None:
    jobs = _load("release.yml")["jobs"]
    assert set(jobs) == {"build", "publish", "verify", "github-release"}
    assert {name: job.get("permissions") for name, job in jobs.items()} == {
        "build": None,
        "publish": {"id-token": "write"},
        "verify": None,
        "github-release": {"contents": "write"},
    }
    assert not any("actions/checkout" in str(s.get("uses", "")) for s in jobs["publish"]["steps"])


def test_each_index_publish_skips_existing_with_attestations() -> None:
    steps = [
        s
        for s in _load("release.yml")["jobs"]["publish"]["steps"]
        if "pypi-publish" in str(s.get("uses", ""))
    ]
    assert {
        s["name"]: (
            s["if"],
            s["with"].get("repository-url"),
            s["with"]["skip-existing"],
            s["with"]["attestations"],
        )
        for s in steps
    } == {
        "Publish to PyPI": ("env.PRODUCTION == 'true'", None, True, True),
        "Publish to TestPyPI": (
            "env.PRODUCTION != 'true'",
            "https://test.pypi.org/legacy/",
            True,
            True,
        ),
    }


def test_build_guards_and_checks_run_before_anything_is_uploaded() -> None:
    steps = _load("release.yml")["jobs"]["build"]["steps"]
    runs = [str(step.get("run", "")) for step in steps]

    def only(needle: str, *, without: str | None = None) -> int:
        """The index of the one step whose script has ``needle`` (and not ``without``)."""
        matches = [
            i
            for i, run in enumerate(runs)
            if needle in run and (without is None or without not in run)
        ]
        assert len(matches) == 1, f"{needle!r} matches steps {matches}, not exactly one"
        return matches[0]

    uploads = [i for i, step in enumerate(steps) if "upload-artifact" in str(step.get("uses"))]
    order = [
        only("SOURCE_DATE_EPOCH"),
        only("release.py version"),
        only("merge-base --is-ancestor"),
        only('release.py check "$VERSION"', without="--dist"),
        only("release.py notes"),
        only("uv build --all-packages"),
        only('check "$VERSION" --dist dist'),
        only("release.py smoke"),
        only("release.py index"),
        min(uploads),
    ]
    assert order == sorted(order)
    assert len(set(order)) == len(order)
    assert steps[0]["with"]["fetch-depth"] == 0
    assert steps[only("merge-base --is-ancestor")]["if"] == "env.PRODUCTION == 'true'"

    build = runs[only("uv build --all-packages")].splitlines()

    def line(needle: str) -> int:
        return next(i for i, text in enumerate(build) if needle in text)

    assert line("rm -rf dist") < line("uv build --all-packages") < line("rm -f dist/.gitignore")


def test_verify_requires_the_complete_index_and_smokes_the_install() -> None:
    job = _load("release.yml")["jobs"]["verify"]
    assert job["needs"] == ["build", "publish"]
    runs = [str(s.get("run", "")) for s in job["steps"]]
    complete = 'release.py index "$VERSION" --dist dist --index "$INDEX" --complete'
    [index] = [i for i, run in enumerate(runs) if complete in run]
    [install] = [
        i
        for i, run in enumerate(runs)
        if 'uv pip install --python "$RUNNER_TEMP/published/bin/python"' in run
        and 'release.py smoke "$RUNNER_TEMP/published/bin/untaped" "$VERSION"' in run
    ]
    assert index < install
    assert "export UV_INDEX" not in runs[install], "scope the index override to uv pip install"


@pytest.mark.parametrize(
    ("workflow", "job"),
    [("release.yml", "build"), ("release.yml", "verify"), ("ci.yml", "unified-app-wheel-smoke")],
)
def test_every_smoke_runs_under_an_isolated_home(workflow: str, job: str) -> None:
    runs = [str(s.get("run", "")) for s in _load(workflow)["jobs"][job]["steps"]]
    home = [i for i, run in enumerate(runs) if 'echo "HOME=$home_dir" >> "$GITHUB_ENV"' in run]
    smoke = [i for i, run in enumerate(runs) if "release.py smoke" in run]
    assert len(home) == 1 and len(smoke) == 1 and home[0] < smoke[0]


def test_github_release_runs_the_script_on_the_built_artifacts() -> None:
    job = _load("release.yml")["jobs"]["github-release"]
    assert job["needs"] == ["build", "verify"]
    runs = [str(s["run"]).strip() for s in job["steps"] if s.get("run")]
    assert runs == [
        'uv run python scripts/release.py github-release "$VERSION" --tag "$TAG"'
        ' --repo "$GITHUB_REPOSITORY" --dist dist --notes release-notes.md'
    ]
    downloads = {
        s["with"]["name"] for s in job["steps"] if "download-artifact" in str(s.get("uses", ""))
    }
    assert downloads == {"dist", "release-notes"}


def test_ci_runs_the_release_check_and_smoke() -> None:
    ci = _load("ci.yml")
    assert ci["on"] == {
        "push": {"branches": ["main"]},
        "pull_request": None,
        "workflow_dispatch": None,
    }
    assert ci["permissions"] == {"contents": "read"}
    gate = "\n".join(str(s.get("run", "")) for s in ci["jobs"]["lint-and-test"]["steps"])
    smoke_steps = ci["jobs"]["unified-app-wheel-smoke"]["steps"]
    smoke = "\n".join(str(s.get("run", "")) for s in smoke_steps)
    assert "scripts/release.py check" in gate
    assert ".github/release" not in gate + smoke
    assert 'release.py smoke "$RUNNER_TEMP/untaped-wheel/bin/untaped"' in smoke


def test_no_run_script_interpolates_expressions() -> None:
    for path in WORKFLOWS:
        for step in _steps(path):
            assert "${{" not in str(step.get("run", "")), (
                f"{path.name}: {step.get('name')} interpolates into shell"
            )
