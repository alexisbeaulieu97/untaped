from collections.abc import Iterator
from pathlib import Path

import pytest
from untaped_core import ConfigError
from untaped_core.settings import get_settings
from untaped_workspace.infrastructure import (
    ManifestRepository,
    WorkspaceRegistryRepository,
    WorkspaceResolver,
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
    ManifestRepository().write(path, _empty_manifest())
    return path


def _empty_manifest() -> object:
    from untaped_workspace.domain import WorkspaceManifest

    return WorkspaceManifest()


def test_resolve_by_name(_isolate: Path, tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path / "prod")
    WorkspaceRegistryRepository().register(name="prod", path=ws)
    resolver = WorkspaceResolver()
    found = resolver.resolve(name="prod")
    assert found.name == "prod"
    assert found.path == ws.resolve()


def test_resolve_by_path_registered(_isolate: Path, tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path / "prod")
    WorkspaceRegistryRepository().register(name="prod", path=ws)
    found = WorkspaceResolver().resolve(path=ws)
    assert found.name == "prod"


def test_resolve_by_path_unregistered_uses_dirname(_isolate: Path, tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path / "lab")
    found = WorkspaceResolver().resolve(path=ws)
    assert found.name == "lab"
    assert found.path == ws.resolve()


def test_resolve_by_path_missing_manifest_errors(_isolate: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ConfigError, match="no workspace manifest"):
        WorkspaceResolver().resolve(path=empty)


def test_resolve_from_cwd_walks_up(_isolate: Path, tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path / "prod")
    sub = ws / "src" / "deep"
    sub.mkdir(parents=True)
    found = WorkspaceResolver().resolve(cwd=sub)
    assert found.path == ws.resolve()


def test_resolve_from_cwd_outside_errors(_isolate: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ConfigError, match="not inside a workspace"):
        WorkspaceResolver().resolve(cwd=outside)
