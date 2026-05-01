"""Unit tests for the manifest-only use cases (init/add/remove/import)."""

from pathlib import Path

import pytest
from untaped_workspace.application import (
    AddRepo,
    ImportWorkspace,
    InitWorkspace,
    RemoveRepo,
)
from untaped_workspace.domain import Workspace
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.infrastructure import ManifestRepository


class _StubRegistry:
    def __init__(self) -> None:
        self.entries: list[Workspace] = []

    def register(self, *, name: str, path: Path) -> Workspace:
        ws = Workspace(name=name, path=path)
        self.entries.append(ws)
        return ws

    def find_by_path(self, path: Path) -> Workspace | None:
        for w in self.entries:
            if w.path == path:
                return w
        return None


def test_init_creates_dir_manifest_and_registers(tmp_path: Path) -> None:
    repo = ManifestRepository()
    reg = _StubRegistry()
    ws_path = tmp_path / "prod"
    result = InitWorkspace(repo, reg)(ws_path, name="prod", branch="main")
    assert result.name == "prod"
    assert (ws_path / "untaped.yml").is_file()
    manifest = repo.read(ws_path)
    assert manifest.name == "prod"
    assert manifest.defaults.branch == "main"
    assert reg.entries[0].name == "prod"


def test_init_derives_name_from_path(tmp_path: Path) -> None:
    InitWorkspace(ManifestRepository(), _StubRegistry())(tmp_path / "lab")
    assert (tmp_path / "lab" / "untaped.yml").is_file()


def test_init_refuses_existing_manifest(tmp_path: Path) -> None:
    ws = tmp_path / "prod"
    ManifestRepository().write(ws, _empty_manifest())
    with pytest.raises(WorkspaceError, match="already initialised"):
        InitWorkspace(ManifestRepository(), _StubRegistry())(ws, name="prod")


def test_add_repo_appends_and_dedups(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, _empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    use_case = AddRepo(ManifestRepository())

    use_case(workspace, url="https://github.com/org/svc-a.git")
    with pytest.raises(WorkspaceError, match="already in workspace"):
        use_case(workspace, url="https://github.com/org/svc-a.git")

    manifest = ManifestRepository().read(ws_path)
    assert len(manifest.repos) == 1
    assert manifest.repos[0].name == "svc-a"


def test_add_repo_with_explicit_name_and_branch(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, _empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(
        workspace,
        url="https://github.com/org/svc-a.git",
        repo_name="alpha",
        branch="develop",
    )
    manifest = ManifestRepository().read(ws_path)
    assert manifest.repos[0].name == "alpha"
    assert manifest.repos[0].branch == "develop"


def test_add_repo_rejects_name_collision(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, _empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://github.com/org/svc-a.git")
    # Same derived name from a different host
    with pytest.raises(WorkspaceError, match="already in use"):
        AddRepo(ManifestRepository())(workspace, url="https://gitlab.com/team/svc-a.git")


def test_remove_repo_by_url(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, _empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://github.com/org/svc-a.git")
    RemoveRepo(ManifestRepository())(workspace, ident="https://github.com/org/svc-a.git")
    assert ManifestRepository().read(ws_path).repos == []


def test_remove_repo_by_alias(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, _empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://x/svc-a.git")
    RemoveRepo(ManifestRepository())(workspace, ident="svc-a")
    assert ManifestRepository().read(ws_path).repos == []


def test_remove_repo_unknown_raises(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, _empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    with pytest.raises(WorkspaceError, match="not declared"):
        RemoveRepo(ManifestRepository())(workspace, ident="nope")


def test_remove_repo_prune_deletes_clone_dir(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, _empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://x/svc-a.git")

    clone_dir = ws_path / "svc-a"
    clone_dir.mkdir()
    (clone_dir / "data.txt").write_text("payload")

    RemoveRepo(ManifestRepository())(workspace, ident="svc-a", prune=True)
    assert not clone_dir.exists()


def test_remove_repo_prune_refuses_dirty(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, _empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://x/svc-a.git")

    clone_dir = ws_path / "svc-a"
    clone_dir.mkdir()

    class _DirtyChecker:
        def is_dirty(self, _: Path) -> bool:
            return True

    use_case = RemoveRepo(ManifestRepository(), status=_DirtyChecker())
    with pytest.raises(WorkspaceError, match="uncommitted changes"):
        use_case(workspace, ident="svc-a", prune=True)
    assert clone_dir.exists()  # not pruned


def test_import_creates_workspace_from_external_manifest(tmp_path: Path) -> None:
    src = tmp_path / "team-prod.yml"
    src.write_text(
        """\
name: prod-template
defaults:
  branch: main
repos:
  - url: https://github.com/org/svc-a.git
"""
    )
    dest = tmp_path / "ws-imported"

    reg = _StubRegistry()
    result = ImportWorkspace(ManifestRepository(), reg)(src, path=dest, name="imported")
    assert result.name == "imported"
    assert (dest / "untaped.yml").is_file()
    loaded = ManifestRepository().read(dest)
    assert loaded.name == "imported"
    assert loaded.repos[0].name == "svc-a"
    assert reg.entries[0].name == "imported"


def test_import_uses_path_dirname_when_no_name(tmp_path: Path) -> None:
    src = tmp_path / "m.yml"
    src.write_text("repos: []\n")
    dest = tmp_path / "auto"
    ImportWorkspace(ManifestRepository(), _StubRegistry())(src, path=dest)
    assert ManifestRepository().read(dest).name == "auto"


def _empty_manifest() -> object:
    from untaped_workspace.domain import WorkspaceManifest

    return WorkspaceManifest()
