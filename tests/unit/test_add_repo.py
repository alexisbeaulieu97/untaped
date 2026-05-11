"""Unit tests for the ``AddRepo`` use case."""

from pathlib import Path

import pytest
from conftest import empty_manifest
from untaped_workspace.application import AddRepo
from untaped_workspace.domain import Workspace
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.infrastructure import ManifestRepository


def test_add_repo_appends_and_dedups(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, empty_manifest())
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
    ManifestRepository().write(ws_path, empty_manifest())
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
    ManifestRepository().write(ws_path, empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://github.com/org/svc-a.git")
    # Same derived name from a different host
    with pytest.raises(WorkspaceError, match="already in use"):
        AddRepo(ManifestRepository())(workspace, url="https://gitlab.com/team/svc-a.git")


def test_add_repo_rejects_explicit_name_collision(tmp_path: Path) -> None:
    """An explicit ``--repo-name`` that collides with an existing repo must
    be rejected before the manifest is mutated. Without this guard the
    Pydantic ``WorkspaceManifest`` validator only fires on the *next*
    read, leaving an invalid YAML on disk that the tool itself wrote.
    """
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://github.com/org/svc-a.git")

    with pytest.raises(WorkspaceError, match="already in use"):
        AddRepo(ManifestRepository())(
            workspace,
            url="https://github.com/team/other.git",
            repo_name="svc-a",
        )

    # Manifest must still be readable and unchanged — no half-written state.
    manifest = ManifestRepository().read(ws_path)
    assert [r.name for r in manifest.repos] == ["svc-a"]
    assert [r.url for r in manifest.repos] == ["https://github.com/org/svc-a.git"]


def test_add_repo_derived_collision_message_suggests_repo_name_flag(tmp_path: Path) -> None:
    """When the collision comes from a *derived* name, point the user at
    the disambiguation flag. The explicit-collision case must NOT show
    this suggestion (the user already used the flag)."""
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://github.com/org/svc-a.git")

    with pytest.raises(WorkspaceError, match="--repo-name"):
        AddRepo(ManifestRepository())(workspace, url="https://gitlab.com/team/svc-a.git")


def test_add_repo_explicit_collision_message_omits_repo_name_flag(tmp_path: Path) -> None:
    ws_path = tmp_path / "prod"
    ManifestRepository().write(ws_path, empty_manifest())
    workspace = Workspace(name="prod", path=ws_path)
    AddRepo(ManifestRepository())(workspace, url="https://github.com/org/svc-a.git")

    with pytest.raises(WorkspaceError) as exc_info:
        AddRepo(ManifestRepository())(
            workspace,
            url="https://github.com/team/other.git",
            repo_name="svc-a",
        )
    assert "--repo-name" not in str(exc_info.value)
