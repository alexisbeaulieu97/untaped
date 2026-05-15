"""Unit tests for the ``ImportWorkspace`` use case.

Collision raises and name-derivation invariants live on
``test_workspace_bootstrapper.py``. This file pins what
``ImportWorkspace`` itself owns: reading the external manifest,
preserving its ``defaults`` + ``repos``, and the
``name → loaded.manifest.name → canonical.name`` precedence (the
``loaded.manifest.name`` rung lives here; the other two ride on the
bootstrapper).
"""

from pathlib import Path

from conftest import StubRegistry
from untaped_workspace.application import ImportWorkspace, WorkspaceBootstrapper
from untaped_workspace.infrastructure import ManifestRepository


def _import(repo: ManifestRepository, reg: StubRegistry) -> ImportWorkspace:
    return ImportWorkspace(repo, WorkspaceBootstrapper(repo, reg))


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

    repo = ManifestRepository()
    reg = StubRegistry()
    result = _import(repo, reg)(src, path=dest, name="imported")
    assert result.name == "imported"
    assert (dest / "untaped.yml").is_file()
    loaded = repo.read(dest)
    assert loaded.name == "imported"
    assert loaded.defaults.branch == "main"
    assert loaded.repos[0].name == "svc-a"
    assert reg.registered[0].name == "imported"


def test_import_prefers_loaded_manifest_name_over_dirname(tmp_path: Path) -> None:
    """No explicit ``--name`` + the external manifest declares ``name:`` →
    that wins over the directory's basename.
    """
    src = tmp_path / "m.yml"
    src.write_text("name: from-manifest\nrepos: []\n")
    dest = tmp_path / "auto"
    repo = ManifestRepository()
    _import(repo, StubRegistry())(src, path=dest)
    assert repo.read(dest).name == "from-manifest"


def test_import_falls_back_to_dirname_when_manifest_has_no_name(tmp_path: Path) -> None:
    src = tmp_path / "m.yml"
    src.write_text("repos: []\n")
    dest = tmp_path / "auto"
    repo = ManifestRepository()
    _import(repo, StubRegistry())(src, path=dest)
    assert repo.read(dest).name == "auto"
