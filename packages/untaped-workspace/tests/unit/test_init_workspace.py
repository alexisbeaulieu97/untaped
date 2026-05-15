"""Unit tests for the ``InitWorkspace`` use case.

Collision raises and name-derivation invariants live on
``test_workspace_bootstrapper.py``. This file pins only what
``InitWorkspace`` itself owns: plumbing ``branch`` into the manifest's
defaults.
"""

from pathlib import Path

from conftest import StubRegistry
from untaped_workspace.application import InitWorkspace, WorkspaceBootstrapper
from untaped_workspace.infrastructure import ManifestRepository


def _init(repo: ManifestRepository, reg: StubRegistry) -> InitWorkspace:
    return InitWorkspace(WorkspaceBootstrapper(repo, reg))


def test_init_creates_dir_manifest_and_registers(tmp_path: Path) -> None:
    repo = ManifestRepository()
    reg = StubRegistry()
    ws_path = tmp_path / "prod"
    result = _init(repo, reg)(ws_path, name="prod", branch="main")
    assert result.name == "prod"
    assert (ws_path / "untaped.yml").is_file()
    manifest = repo.read(ws_path)
    assert manifest.name == "prod"
    assert manifest.defaults.branch == "main"
    assert reg.registered[0].name == "prod"


def test_init_without_branch_leaves_defaults_branch_unset(tmp_path: Path) -> None:
    repo = ManifestRepository()
    _init(repo, StubRegistry())(tmp_path / "lab")
    assert repo.read(tmp_path / "lab").defaults.branch is None
