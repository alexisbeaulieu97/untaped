from pathlib import Path

import pytest
from conftest import StubGit

from untaped_workspace.application import WorkspaceStatus
from untaped_workspace.domain import (
    Repo,
    RepoStatus,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import WorkspaceError
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
