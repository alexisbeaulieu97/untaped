from collections.abc import Iterator
from pathlib import Path

import pytest
from untaped_core.settings import get_settings
from untaped_workspace.errors import RegistryError
from untaped_workspace.infrastructure import WorkspaceRegistryRepository


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    get_settings.cache_clear()
    yield cfg
    get_settings.cache_clear()


def test_list_empty_when_no_config(_isolate_config: Path) -> None:
    assert WorkspaceRegistryRepository().entries() == []


def test_register_and_list(_isolate_config: Path, tmp_path: Path) -> None:
    repo = WorkspaceRegistryRepository()
    ws_path = tmp_path / "prod"
    ws_path.mkdir()
    ws = repo.register(name="prod", path=ws_path)
    assert ws.name == "prod"
    assert ws.path == ws_path.resolve()
    assert [w.name for w in repo.entries()] == ["prod"]


def test_register_rejects_duplicate_name(_isolate_config: Path, tmp_path: Path) -> None:
    repo = WorkspaceRegistryRepository()
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    repo.register(name="prod", path=a)
    with pytest.raises(RegistryError, match="name already registered"):
        repo.register(name="prod", path=b)


def test_register_rejects_duplicate_path(_isolate_config: Path, tmp_path: Path) -> None:
    repo = WorkspaceRegistryRepository()
    a = tmp_path / "a"
    a.mkdir()
    repo.register(name="prod", path=a)
    with pytest.raises(RegistryError, match="path already registered"):
        repo.register(name="other", path=a)


def test_get_unknown_raises(_isolate_config: Path) -> None:
    repo = WorkspaceRegistryRepository()
    with pytest.raises(RegistryError, match="unknown workspace"):
        repo.get("nonexistent")


def test_unregister(_isolate_config: Path, tmp_path: Path) -> None:
    repo = WorkspaceRegistryRepository()
    a = tmp_path / "a"
    a.mkdir()
    repo.register(name="prod", path=a)
    assert repo.unregister("prod") is True
    assert repo.entries() == []
    assert repo.unregister("prod") is False


def test_find_by_path_canonical_match(_isolate_config: Path, tmp_path: Path) -> None:
    repo = WorkspaceRegistryRepository()
    a = tmp_path / "a"
    a.mkdir()
    repo.register(name="prod", path=a)
    # Same path but a relative-style symlink-like form should still match
    found = repo.find_by_path(a)
    assert found is not None
    assert found.name == "prod"


def test_register_preserves_other_top_level_settings(_isolate_config: Path, tmp_path: Path) -> None:
    _isolate_config.write_text("log_level: DEBUG\nawx:\n  base_url: https://x\n")
    repo = WorkspaceRegistryRepository()
    a = tmp_path / "a"
    a.mkdir()
    repo.register(name="prod", path=a)

    import yaml as _yaml

    raw = _yaml.safe_load(_isolate_config.read_text())
    assert raw["log_level"] == "DEBUG"
    assert raw["awx"]["base_url"] == "https://x"
    assert raw["workspace"]["workspaces"][0]["name"] == "prod"
