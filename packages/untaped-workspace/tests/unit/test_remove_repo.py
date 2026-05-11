"""Unit tests for the ``RemoveRepo`` use case."""

from pathlib import Path

import pytest
from conftest import empty_manifest
from untaped_workspace.application import AddRepo, RemoveRepo
from untaped_workspace.domain import Workspace
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.infrastructure import LocalFilesystem, ManifestRepository

_FS = LocalFilesystem()


def test_remove_repo_by_url(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://github.com/org/svc-a.git")
    RemoveRepo(ManifestRepository(), fs=_FS)(workspace, ident="https://github.com/org/svc-a.git")
    assert ManifestRepository().read(ws_path).repos == []


def test_remove_repo_by_alias(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://x/svc-a.git")
    RemoveRepo(ManifestRepository(), fs=_FS)(workspace, ident="svc-a")
    assert ManifestRepository().read(ws_path).repos == []


def test_remove_repo_unknown_raises(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    with pytest.raises(WorkspaceError, match="not declared"):
        RemoveRepo(ManifestRepository(), fs=_FS)(workspace, ident="nope")


def test_remove_repo_prune_deletes_clone_dir(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://x/svc-a.git")

    clone_dir = ws_path / "svc-a"
    clone_dir.mkdir()
    (clone_dir / "data.txt").write_text("payload")

    RemoveRepo(ManifestRepository(), fs=_FS)(workspace, ident="svc-a", prune=True)
    assert not clone_dir.exists()


def test_remove_repo_prune_refuses_dirty(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://x/svc-a.git")

    clone_dir = ws_path / "svc-a"
    clone_dir.mkdir()

    class _DirtyChecker:
        def is_dirty(self, _: Path) -> bool:
            return True

    use_case = RemoveRepo(ManifestRepository(), fs=_FS, status=_DirtyChecker())
    with pytest.raises(WorkspaceError, match="uncommitted changes"):
        use_case(workspace, ident="svc-a", prune=True)
    assert clone_dir.exists()  # not pruned
    # manifest must not have been modified — both clone and entry stay
    manifest = ManifestRepository().read(ws_path)
    assert [r.name for r in manifest.repos] == ["svc-a"]
