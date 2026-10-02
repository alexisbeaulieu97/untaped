"""``awx apply --source-ref REF PATH…``: apply documents as they are at a commit."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from untaped.testing import CliInvoker
from untaped_awx.cli import app

pytestmark = pytest.mark.integration

_TEMPLATE = """\
kind: JobTemplate
metadata: {name: Deploy, organization: Default}
spec: {playbook: deploy.yml, description: %s}
"""
_WORKFLOW = """\
kind: WorkflowJobTemplate
metadata: {name: Release, organization: Default}
spec:
  nodes:
    - id: deploy
      run: {job_template: Deploy}
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Specs committed and tagged ``v1``; the working tree has moved on since."""
    repo = tmp_path / "playbooks"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    for key, value in [("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")]:
        _git(repo, "config", key, value)
    root = repo / ".untaped" / "awx"
    (root / "templates").mkdir(parents=True)
    (root / "workflows").mkdir()
    (root / "templates" / "deploy.yml").write_text(_TEMPLATE % "from-v1")
    (root / "workflows" / "release.yml").write_text(_WORKFLOW)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "specs")
    _git(repo, "tag", "v1")
    (root / "templates" / "deploy.yml").write_text(_TEMPLATE % "working-tree")
    monkeypatch.chdir(repo)
    return repo


def _apply(*args: str) -> Any:
    return CliInvoker().invoke(app, ["apply", *args])


def test_documents_are_read_at_the_ref_and_applied_in_order(
    seeded_default_org: Any, repo: Path
) -> None:
    result = _apply(
        "--source-ref", "v1", ".untaped/awx/workflows", ".untaped/awx/templates", "--yes"
    )

    assert result.exit_code == 0, result.output + (result.stderr or "")
    (template,) = seeded_default_org.list_records("job_templates")
    assert template["description"] == "from-v1"
    (node,) = seeded_default_org.list_records("workflow_nodes")
    assert node["unified_job_template"] == template["id"]


def test_without_a_ref_several_paths_read_the_working_tree(
    seeded_default_org: Any, repo: Path
) -> None:
    result = _apply(".untaped/awx/templates/deploy.yml", ".untaped/awx/workflows", "--yes")

    assert result.exit_code == 0, result.output + (result.stderr or "")
    (template,) = seeded_default_org.list_records("job_templates")
    assert template["description"] == "working-tree"


def test_errors_name_the_file_at_the_ref(seeded_default_org: Any, repo: Path) -> None:
    bad = repo / ".untaped" / "awx" / "templates" / "bad.yml"
    bad.write_text("kind: Nope\nmetadata: {name: x}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "bad")

    result = _apply("--source-ref", "main", ".untaped/awx/templates", "--dry-run")

    assert result.exit_code == 1
    assert "main:.untaped/awx/templates/bad.yml: unknown kind 'Nope'" in (result.stderr or "")


def test_stdin_cannot_be_read_at_a_ref(seeded_default_org: Any, repo: Path) -> None:
    result = _apply("--source-ref", "v1", "-")

    assert result.exit_code == 2
    assert "--source-ref reads files at a commit; it cannot read stdin" in (
        result.stderr or result.output
    )
