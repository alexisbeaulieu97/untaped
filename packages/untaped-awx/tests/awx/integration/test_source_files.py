"""Suites and template specs read at one commit for ``awx test --source-ref``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from untaped.sdk import ConfigError
from untaped_awx.infrastructure.git_source import GitSource
from untaped_awx.infrastructure.suites.source_files import (
    GitSuiteFiles,
    template_specs,
)

_SUITE = (
    "kind: AwxTestSuite\njobTemplate: Deploy\n"
    "cases: {smoke: {launch: {extra_vars: {jt: !ref {kind: JobTemplate, name: X}}}}}\n"
)
_TEMPLATE = "kind: JobTemplate\nmetadata: {name: Deploy, organization: Default}\nspec: {}\n"
_WORKFLOW = "kind: WorkflowJobTemplate  # the release\nmetadata: {name: Release}\nspec: {}\n"
_FLOW = "{kind: JobTemplate, metadata: {name: Smoke}, spec: {}}\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Suites, specs and other YAML committed on ``main``; the working tree has moved on."""
    repo = tmp_path / "playbooks"
    root = repo / ".untaped" / "awx"
    for sub in ("tests/.hidden", "templates", "workflows", "fixtures"):
        (root / sub).mkdir(parents=True)
    _git(repo, "init", "--initial-branch=main")
    for key, value in [("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")]:
        _git(repo, "config", key, value)
    (root / "tests" / "deploy.yml").write_text(_SUITE)
    (root / "tests" / "vars.yml").write_text("env: prod\n")
    (root / "tests" / ".hidden" / "skip.yml").write_text(_SUITE)
    (root / "templates" / "deploy.yml").write_text(_TEMPLATE)
    (root / "workflows" / "release.yaml").write_text(_WORKFLOW)
    (root / "templates" / "smoke.yml").write_text(_FLOW)
    (root / "fixtures" / "project.yml").write_text(
        "kind: Project\nmetadata: {name: Playbooks}\nspec: {}\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "one")
    (root / "tests" / "deploy.yml").write_text("kind: AwxTestSuite\nbroken: [\n")
    monkeypatch.chdir(repo)
    return repo


def test_a_directory_lists_its_suites_at_the_commit(repo: Path) -> None:
    files = GitSuiteFiles(GitSource.resolve("main"))

    [path] = files.suites([Path(".untaped/awx/tests")])

    assert path == Path("main:.untaped/awx/tests/deploy.yml")
    assert path.stem == "deploy"
    assert files.read_text(path) == _SUITE


def test_a_file_named_directly_is_read_whatever_it_holds(repo: Path) -> None:
    files = GitSuiteFiles(GitSource.resolve("main"))

    [path] = files.suites([Path(".untaped/awx/tests/vars.yml")])

    assert files.read_text(path) == "env: prod\n"


def test_a_directory_without_suites_is_refused(repo: Path) -> None:
    files = GitSuiteFiles(GitSource.resolve("main"))

    with pytest.raises(ConfigError, match=r"no test suites under \.untaped/awx/templates at main"):
        files.suites([Path(".untaped/awx/templates")])


def test_template_specs_are_the_job_templates_and_workflows_anywhere_under_the_root(
    repo: Path,
) -> None:
    specs = template_specs(GitSource.resolve("main"))

    assert [(path, doc.kind, doc.metadata.name) for path, doc in specs] == [
        ("main:.untaped/awx/templates/deploy.yml", "JobTemplate", "Deploy"),
        ("main:.untaped/awx/templates/smoke.yml", "JobTemplate", "Smoke"),
        ("main:.untaped/awx/workflows/release.yaml", "WorkflowJobTemplate", "Release"),
    ]


def test_a_commit_without_specs_has_none(repo: Path) -> None:
    _git(repo, "rm", "-rqf", ".untaped")
    _git(repo, "commit", "-qm", "gone")

    assert template_specs(GitSource.resolve("main")) == []
