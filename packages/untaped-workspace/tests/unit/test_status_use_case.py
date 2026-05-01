from pathlib import Path

from untaped_workspace.application import WorkspaceStatus
from untaped_workspace.domain import (
    Repo,
    RepoStatus,
    Workspace,
    WorkspaceManifest,
)
from untaped_workspace.errors import GitError
from untaped_workspace.infrastructure import ManifestRepository


class _StubGit:
    def __init__(self, statuses: dict[str, RepoStatus | GitError]) -> None:
        self._statuses = statuses

    def status(self, repo_path: Path) -> RepoStatus:
        result = self._statuses.get(repo_path.name)
        if isinstance(result, GitError):
            raise result
        if result is None:
            raise GitError(f"no stub status for {repo_path.name}")
        return result


def _seed(tmp_path: Path, manifest: WorkspaceManifest) -> Workspace:
    ws = tmp_path / "prod"
    ws.mkdir()
    ManifestRepository().write(ws, manifest)
    return Workspace(name="prod", path=ws)


def test_reports_not_cloned_when_dir_missing(tmp_path: Path) -> None:
    workspace = _seed(tmp_path, WorkspaceManifest(repos=[Repo(url="https://x/a.git")]))
    git = _StubGit({})
    entries = WorkspaceStatus(ManifestRepository(), git)(workspace)
    assert entries[0].cloned is False


def test_reports_status_for_cloned_repos(tmp_path: Path) -> None:
    workspace = _seed(
        tmp_path,
        WorkspaceManifest(repos=[Repo(url="https://x/a.git"), Repo(url="https://x/b.git")]),
    )
    (workspace.path / "a").mkdir()
    (workspace.path / "b").mkdir()
    git = _StubGit(
        {
            "a": RepoStatus(branch="main", behind=2),
            "b": RepoStatus(branch="develop", modified=1, untracked=2),
        }
    )
    entries = WorkspaceStatus(ManifestRepository(), git)(workspace)
    by_repo = {e.repo: e for e in entries}
    assert by_repo["a"].cloned and by_repo["a"].behind == 2
    assert by_repo["b"].modified == 1
    assert by_repo["b"].untracked == 2


def test_git_error_marks_not_cloned(tmp_path: Path) -> None:
    workspace = _seed(tmp_path, WorkspaceManifest(repos=[Repo(url="https://x/a.git")]))
    (workspace.path / "a").mkdir()
    git = _StubGit({"a": GitError("not a git dir")})
    entries = WorkspaceStatus(ManifestRepository(), git)(workspace)
    assert entries[0].cloned is False
