"""Unit tests for the ``InitWorkspace`` use case."""

from pathlib import Path

import pytest
from conftest import StubRegistry, empty_manifest
from untaped_workspace.application import InitWorkspace
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.infrastructure import LocalFilesystem, ManifestRepository

_FS = LocalFilesystem()


def test_init_creates_dir_manifest_and_registers(tmp_path: Path) -> None:
    repo = ManifestRepository()
    reg = StubRegistry()
    ws_path = tmp_path / "prod"
    result = InitWorkspace(repo, reg, fs=_FS)(ws_path, name="prod", branch="main")
    assert result.name == "prod"
    assert (ws_path / "untaped.yml").is_file()
    manifest = repo.read(ws_path)
    assert manifest.name == "prod"
    assert manifest.defaults.branch == "main"
    assert reg.registered[0].name == "prod"


def test_init_derives_name_from_path(tmp_path: Path) -> None:
    InitWorkspace(ManifestRepository(), StubRegistry(), fs=_FS)(tmp_path / "lab")
    assert (tmp_path / "lab" / "untaped.yml").is_file()


def test_init_refuses_existing_manifest(tmp_path: Path) -> None:
    ws = tmp_path / "prod"
    ManifestRepository().write(ws, empty_manifest())
    with pytest.raises(WorkspaceError, match="already initialised"):
        InitWorkspace(ManifestRepository(), StubRegistry(), fs=_FS)(ws, name="prod")
