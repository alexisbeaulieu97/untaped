from pathlib import Path

import pytest
from conftest import StubGit, StubManifests

from untaped_workspace.application import WorkspaceStatus
from untaped_workspace.domain import (
    Repo,
    RepoStatus,
    StatusEntry,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import ManifestError, WorkspaceError
from untaped_workspace.infrastructure import LocalFilesystem, ManifestRepository

_FS = LocalFilesystem()


def _seed(tmp_path: Path, manifest: WorkspaceManifest) -> Workspace:
    ws = tmp_path / "prod"
    ws.mkdir()
    ManifestRepository().write(ws, manifest)
    return Workspace(name="prod", path=ws)


def test_reports_not_cloned_when_dir_missing(tmp_path: Path) -> None:
    workspace = _seed(tmp_path, WorkspaceManifest(repos=[Repo(url="https://x/a.git")]))
    git = StubGit()
    entries = WorkspaceStatus(ManifestRepository(), git, fs=_FS)(workspace)
    assert entries[0].cloned is False
    assert entries[0].action == "status"


def test_reports_status_for_cloned_repos(tmp_path: Path) -> None:
    workspace = _seed(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/a.git"), Repo(url="https://x/b.git")]),
    )
    (workspace.path / "a").mkdir()
    (workspace.path / "b").mkdir()
    git = StubGit(
        statuses={
            "a": RepoStatus(branch="main", behind=2),
            "b": RepoStatus(branch="develop", modified=1, untracked=2),
        }
    )
    entries = WorkspaceStatus(ManifestRepository(), git, fs=_FS)(workspace)
    by_repo = {e.repo: e for e in entries}
    assert by_repo["a"].cloned and by_repo["a"].behind == 2
    assert by_repo["b"].modified == 1
    assert by_repo["b"].untracked == 2


def test_git_error_marks_not_cloned(tmp_path: Path) -> None:
    workspace = _seed(tmp_path, WorkspaceManifest(repos=[Repo(url="https://x/a.git")]))
    (workspace.path / "a").mkdir()
    git = StubGit(status_fail={"a"})
    entries = WorkspaceStatus(ManifestRepository(), git, fs=_FS)(workspace)
    assert entries[0].cloned is False


def test_filters_by_repo_name(tmp_path: Path) -> None:
    workspace = _seed(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/a.git"), Repo(url="https://x/b.git")]),
    )
    git = StubGit()

    entries = WorkspaceStatus(ManifestRepository(), git, fs=_FS)(workspace, only=["b"])

    assert [entry.repo for entry in entries] == ["b"]


def test_unknown_repo_filter_raises_before_git_status(tmp_path: Path) -> None:
    workspace = _seed(tmp_path, WorkspaceManifest(repos=[Repo(url="https://x/a.git")]))
    git = StubGit()

    with pytest.raises(WorkspaceError, match="ghost"):
        WorkspaceStatus(ManifestRepository(), git, fs=_FS)(workspace, only=["ghost"])

    assert git.events == []


def test_skip_manifest_errors_returns_unavailable_status_row(tmp_path: Path) -> None:
    workspace = Workspace(name="ghost", path=tmp_path / "ghost")

    entries = WorkspaceStatus(StubManifests(), StubGit(), fs=_FS)(
        workspace,
        skip_manifest_errors=True,
    )

    assert entries == [
        StatusEntry(
            workspace="ghost",
            repo="",
            action="unavailable",
            detail=(f"workspace manifest unavailable: no manifest at {workspace.path}/untaped.yml"),
            cloned=False,
        )
    ]


def test_skip_manifest_errors_returns_unavailable_for_invalid_manifest(tmp_path: Path) -> None:
    workspace = Workspace(name="ghost", path=tmp_path / "ghost")

    class _InvalidManifests(StubManifests):
        def read(self, workspace_dir: Path) -> WorkspaceManifest:
            raise ManifestError(f"invalid manifest at {workspace_dir}/untaped.yml: repos")

    entries = WorkspaceStatus(_InvalidManifests(), StubGit(), fs=_FS)(
        workspace,
        skip_manifest_errors=True,
    )

    assert entries[0].workspace == "ghost"
    assert entries[0].repo == ""
    assert entries[0].action == "unavailable"
    assert entries[0].detail == (
        f"workspace manifest unavailable: invalid manifest at {workspace.path}/untaped.yml: repos"
    )


def test_manifest_error_stays_strict_by_default(tmp_path: Path) -> None:
    workspace = Workspace(name="ghost", path=tmp_path / "ghost")

    with pytest.raises(WorkspaceError, match="no manifest"):
        WorkspaceStatus(StubManifests(), StubGit(), fs=_FS)(workspace)
