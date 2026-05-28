"""Unit tests for the ForgetWorkspace use case."""

from pathlib import Path

import pytest
from untaped_workspace.application import ForgetWorkspace
from untaped_workspace.domain import Workspace
from untaped_workspace.errors import GitError, RegistryError, WorkspaceError
from untaped_workspace.infrastructure import LocalFilesystem, ManifestRepository

from conftest import StubRegistry


class _StubStatus:
    def __init__(self, dirty: set[Path] | None = None) -> None:
        self._dirty = dirty or set()

    def is_dirty(self, repo_path: Path) -> bool:
        return repo_path in self._dirty


def _seed_manifest(ws_path: Path, *, repos: list[tuple[str, str]] = ()) -> None:
    """Write a workspace manifest with the given (name, url) repos."""
    from untaped_workspace.domain import Repo, WorkspaceManifest

    manifest = WorkspaceManifest(
        name=ws_path.name,
        repos=[Repo(url=url, name=name) for name, url in repos],
    )
    ManifestRepository().write(ws_path, manifest)


def test_forget_removes_registry_entry(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    ws = Workspace(name="prod", path=ws_path)
    reg = StubRegistry([ws])

    result = ForgetWorkspace(reg, ManifestRepository(), fs=LocalFilesystem(), status=_StubStatus())(
        "prod"
    )

    assert result.name == "prod"
    assert reg.registered == []
    assert reg.unregistered == ["prod"]
    assert ws_path.is_dir()  # files preserved


def test_forget_unknown_workspace_raises(tmp_path: Path) -> None:
    reg = StubRegistry([])
    with pytest.raises(RegistryError, match="unknown workspace"):
        ForgetWorkspace(reg, ManifestRepository(), fs=LocalFilesystem(), status=_StubStatus())(
            "ghost"
        )


def test_forget_with_prune_deletes_workspace_dir(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    (ws_path / "marker.txt").write_text("hi")
    _seed_manifest(ws_path)
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])

    ForgetWorkspace(reg, ManifestRepository(), fs=LocalFilesystem(), status=_StubStatus())(
        "prod", prune=True
    )

    assert not ws_path.exists()
    assert reg.registered == []


def test_forget_prune_refuses_dirty_repo(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    repo_dir = ws_path / "svc-a"
    repo_dir.mkdir()
    _seed_manifest(ws_path, repos=[("svc-a", "https://x/svc-a.git")])
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])
    status = _StubStatus(dirty={repo_dir})

    with pytest.raises(WorkspaceError, match="uncommitted changes"):
        ForgetWorkspace(reg, ManifestRepository(), fs=LocalFilesystem(), status=status)(
            "prod", prune=True
        )

    assert ws_path.is_dir()  # untouched
    assert reg.registered  # registry untouched


def test_forget_prune_succeeds_when_path_missing(tmp_path: Path) -> None:
    ws_path = tmp_path / "ghost"  # never created
    reg = StubRegistry([Workspace(name="ghost", path=ws_path)])

    ForgetWorkspace(reg, ManifestRepository(), fs=LocalFilesystem(), status=_StubStatus())(
        "ghost", prune=True
    )

    assert reg.registered == []


def test_forget_succeeds_when_manifest_missing(tmp_path: Path) -> None:
    """Without ``--prune``, a missing manifest is tolerated — the registry
    entry is removed regardless. Files on disk are preserved.
    """
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    (ws_path / "stranded.txt").write_text("no manifest here")
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])

    ForgetWorkspace(reg, ManifestRepository(), fs=LocalFilesystem(), status=_StubStatus())("prod")

    assert reg.registered == []
    assert ws_path.is_dir()  # preserved


def test_forget_prune_refuses_when_manifest_missing(tmp_path: Path) -> None:
    """With ``--prune``, refuse to rmtree if we can't read the manifest —
    we have no way to verify cleanliness, so the user is asked to delete
    the directory manually.
    """
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    (ws_path / "stranded.txt").write_text("no manifest here")
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])

    with pytest.raises(WorkspaceError, match="no manifest"):
        ForgetWorkspace(reg, ManifestRepository(), fs=LocalFilesystem(), status=_StubStatus())(
            "prod", prune=True
        )

    assert ws_path.is_dir()  # untouched
    assert reg.registered  # registry untouched


def test_forget_prune_refuses_when_declared_repo_is_not_a_clone(tmp_path: Path) -> None:
    """If a manifest declares a directory that exists but isn't a git
    clone, ``is_dirty`` raises ``GitError``. Refuse with a clear message
    rather than letting the raw subprocess error escape.
    """
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    (ws_path / "svc-a").mkdir()  # exists but no .git
    _seed_manifest(ws_path, repos=[("svc-a", "https://x/svc-a.git")])
    reg = StubRegistry([Workspace(name="prod", path=ws_path)])

    class _ExplodingStatus:
        def is_dirty(self, repo_path: Path) -> bool:
            raise GitError("git status failed: not a git repository")

    with pytest.raises(WorkspaceError, match="cannot inspect 'svc-a'"):
        ForgetWorkspace(reg, ManifestRepository(), fs=LocalFilesystem(), status=_ExplodingStatus())(
            "prod", prune=True
        )

    assert ws_path.is_dir()  # untouched
    assert reg.registered  # registry untouched
