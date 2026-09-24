"""Unit tests for the ``RemoveRepo`` use case."""

from pathlib import Path

import pytest

from untaped.capabilities.workspace.application import RemoveRepo
from untaped.capabilities.workspace.domain import Repo, Workspace, WorkspaceManifest
from untaped.capabilities.workspace.errors import GitError, WorkspaceError
from untaped.capabilities.workspace.infrastructure import LocalFilesystem, YamlManifestRepository


class _PruneSafety:
    def __init__(
        self,
        blockers: dict[Path, tuple[str, ...]] | None = None,
        failures: set[Path] | None = None,
    ) -> None:
        self._blockers = blockers or {}
        self._failures = failures or set()

    def prune_blockers(self, repo_path: Path) -> tuple[str, ...]:
        if repo_path in self._failures:
            raise GitError("not a git repository")
        return self._blockers.get(repo_path, ())


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    manifest = WorkspaceManifest(repos=[Repo(url="https://x/svc-a.git")])
    YamlManifestRepository().write(tmp_path / "prod", manifest)
    return Workspace(name="prod", path=tmp_path / "prod")


def _remove(workspace: Workspace, safety: _PruneSafety | None = None, **kwargs: object) -> None:
    RemoveRepo(
        YamlManifestRepository(), fs=LocalFilesystem(), prune_safety=safety or _PruneSafety()
    )(workspace, **kwargs)


def _names(workspace: Workspace) -> list[str]:
    return [r.name for r in YamlManifestRepository().read(workspace.path).repos]


@pytest.mark.parametrize("ident", ["https://x/svc-a.git", "svc-a"])
def test_remove_repo_by_url_or_name(workspace: Workspace, ident: str) -> None:
    _remove(workspace, ident=ident)
    assert _names(workspace) == []


def test_remove_repo_unknown_raises(workspace: Workspace) -> None:
    with pytest.raises(WorkspaceError, match="not declared"):
        _remove(workspace, ident="nope")


def test_remove_repo_prune_deletes_clone_dir(workspace: Workspace) -> None:
    clone_dir = workspace.path / "svc-a"
    clone_dir.mkdir()
    (clone_dir / "data.txt").write_text("payload")

    _remove(workspace, ident="svc-a", prune=True)

    assert not clone_dir.exists()
    assert _names(workspace) == []


@pytest.mark.parametrize(
    ("inspection_fails", "match"),
    # A failed inspection is translated, not leaked as a raw GitError.
    [(False, "unsafe local state"), (True, "cannot inspect")],
)
def test_remove_repo_prune_refuses_before_any_change(
    workspace: Workspace, inspection_fails: bool, match: str
) -> None:
    clone_dir = workspace.path / "svc-a"
    clone_dir.mkdir()
    if inspection_fails:
        safety = _PruneSafety(failures={clone_dir})
    else:
        safety = _PruneSafety(blockers={clone_dir: ("local commits not reachable",)})

    with pytest.raises(WorkspaceError, match=match):
        _remove(workspace, safety, ident="svc-a", prune=True)

    assert clone_dir.exists()
    assert _names(workspace) == ["svc-a"]
