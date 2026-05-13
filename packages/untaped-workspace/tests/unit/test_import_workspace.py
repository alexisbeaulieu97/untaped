"""Unit tests for the ``ImportWorkspace`` use case."""

from pathlib import Path

import pytest
from conftest import StubRegistry
from untaped_workspace.application import ImportWorkspace
from untaped_workspace.domain import Workspace
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.infrastructure import LocalFilesystem, ManifestRepository

_FS = LocalFilesystem()


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

    reg = StubRegistry()
    result = ImportWorkspace(ManifestRepository(), reg, fs=_FS)(src, path=dest, name="imported")
    assert result.name == "imported"
    assert (dest / "untaped.yml").is_file()
    loaded = ManifestRepository().read(dest)
    assert loaded.name == "imported"
    assert loaded.repos[0].name == "svc-a"
    assert reg.registered[0].name == "imported"


def test_import_uses_path_dirname_when_no_name(tmp_path: Path) -> None:
    src = tmp_path / "m.yml"
    src.write_text("repos: []\n")
    dest = tmp_path / "auto"
    ImportWorkspace(ManifestRepository(), StubRegistry(), fs=_FS)(src, path=dest)
    assert ManifestRepository().read(dest).name == "auto"


def test_import_raises_when_name_cannot_be_derived(tmp_path: Path) -> None:
    """No explicit name + manifest with no ``name:`` + path with no
    derivable name → refuse rather than register a nameless workspace."""
    src = tmp_path / "m.yml"
    src.write_text("repos: []\n")
    reg = StubRegistry()
    with pytest.raises(WorkspaceError, match="unable to derive workspace name"):
        ImportWorkspace(ManifestRepository(), reg, fs=_FS)(src, path=Path("/"))
    assert reg.registered == []  # guard runs before register()


def test_import_refuses_already_initialised_path(tmp_path: Path) -> None:
    """Target path already has an ``untaped.yml`` — refuse rather than
    silently overwriting."""
    src = tmp_path / "src.yml"
    src.write_text("repos: []\n")
    dest = tmp_path / "existing"
    dest.mkdir()
    original = "name: original\nrepos: []\n"
    (dest / "untaped.yml").write_text(original)
    with pytest.raises(WorkspaceError, match="already initialised"):
        ImportWorkspace(ManifestRepository(), StubRegistry(), fs=_FS)(src, path=dest, name="x")
    assert (dest / "untaped.yml").read_text() == original  # existing manifest untouched


def test_import_refuses_path_already_registered(tmp_path: Path) -> None:
    """Registry already has this path — refuse rather than registering a
    second entry for the same directory."""
    src = tmp_path / "src.yml"
    src.write_text("repos: []\n")
    dest = tmp_path / "dest"
    dest.mkdir()  # ensure dest.resolve() == path.expanduser().resolve() inside the use case
    reg = StubRegistry()
    reg.registered.append(Workspace(name="other", path=dest.resolve()))
    with pytest.raises(WorkspaceError, match="path already registered"):
        ImportWorkspace(ManifestRepository(), reg, fs=_FS)(src, path=dest, name="x")
    assert len(reg.registered) == 1  # no second entry written past the guard
