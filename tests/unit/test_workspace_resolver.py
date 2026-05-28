from collections.abc import Iterator
from pathlib import Path

import pytest
from untaped_workspace.application import WorkspaceResolver
from untaped_workspace.domain import WorkspaceManifest
from untaped_workspace.infrastructure import (
    ManifestRepository,
    WorkspaceRegistryRepository,
)

from conftest import empty_manifest
from untaped import ConfigError
from untaped.settings import get_settings


def _resolver() -> WorkspaceResolver:
    return WorkspaceResolver(
        registry=WorkspaceRegistryRepository(),
        manifests=ManifestRepository(),
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()
    yield cfg
    get_settings.cache_clear()


def _make_workspace(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    ManifestRepository().write(path, empty_manifest())
    return path


def test_resolve_by_name(_isolate: Path, tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path / "prod")
    WorkspaceRegistryRepository().register(name="prod", path=ws)
    resolver = _resolver()
    found = resolver.resolve(name="prod")
    assert found.name == "prod"
    assert found.path == ws.resolve()


def test_resolve_by_path_registered(_isolate: Path, tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path / "prod")
    WorkspaceRegistryRepository().register(name="prod", path=ws)
    found = _resolver().resolve(path=ws)
    assert found.name == "prod"


def test_resolve_by_path_unregistered_falls_back_to_dirname_when_manifest_has_no_name(
    _isolate: Path,
    tmp_path: Path,
) -> None:
    """No registry entry + ``_make_workspace`` writes an
    ``empty_manifest()`` (``name=None``) → dirname wins.
    """
    ws = _make_workspace(tmp_path / "lab")
    found = _resolver().resolve(path=ws)
    assert found.name == "lab"
    assert found.path == ws.resolve()


def test_resolve_by_path_unregistered_prefers_manifest_name(_isolate: Path, tmp_path: Path) -> None:
    """Round-trip via the real ``ManifestRepository``: a manifest with
    ``name: bar`` at directory ``foo/`` resolves to ``Workspace.name
    == "bar"``. Pins the precedence end-to-end so a regression that
    affects only the disk-touching path can't slip past the stub tests.
    """
    ws_dir = tmp_path / "foo"
    ws_dir.mkdir()
    ManifestRepository().write(ws_dir, WorkspaceManifest(name="bar"))
    found = _resolver().resolve(path=ws_dir)
    assert found.name == "bar"
    assert found.path == ws_dir.resolve()


def test_resolve_by_path_missing_manifest_errors(_isolate: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ConfigError, match="no workspace manifest"):
        _resolver().resolve(path=empty)


def test_resolve_from_cwd_walks_up(_isolate: Path, tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path / "prod")
    sub = ws / "src" / "deep"
    sub.mkdir(parents=True)
    found = _resolver().resolve(cwd=sub)
    assert found.path == ws.resolve()
    # Mirror the stubs-side cwd-walk tests: pin that the synthesised
    # Workspace.name reflects the resolver's precedence (here: empty
    # manifest → dirname fallback).
    assert found.name == "prod"


def test_resolve_from_cwd_outside_errors(_isolate: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ConfigError, match="not inside a workspace"):
        _resolver().resolve(cwd=outside)
