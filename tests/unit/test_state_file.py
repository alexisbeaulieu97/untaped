"""Capability state lives in ``state.yml``, separate from ``config.yml``.

Covers path resolution, the legacy fallback (state still at the top level of
``config.yml``) with its one-time warning, the move on the first state write,
and the isolation between settings writes and state writes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

import untaped.config_file as config_file
from untaped.config.repository import SettingsFileRepository
from untaped.config_file import mutate_tool_state, read_tool_state
from untaped.errors import ConfigError
from untaped.settings import (
    get_config_section,
    get_settings,
    register_profile_settings,
    register_state_settings,
    reset_config_registry_for_tests,
    resolve_state_path,
)
from untaped.state import StateCollection

LEGACY = """\
# my config
profiles:
  default:
    demo:
      url: https://example.com   # keep me

# old state layout
demo:
  items:
    - name: alpha
"""


class DemoProfile(BaseModel):
    url: str | None = None


class DemoState(BaseModel):
    items: list[dict[str, Any]] = []


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(path))
    reset_config_registry_for_tests()
    yield path
    reset_config_registry_for_tests()
    get_settings.cache_clear()


def _state(cfg: Path) -> dict[str, Any]:
    state_file = cfg.parent / "state.yml"
    if not state_file.exists():
        return {}
    return yaml.safe_load(state_file.read_text(encoding="utf-8")) or {}


# ── path resolution ──────────────────────────────────────────────────────────


def test_state_path_defaults_next_to_config(cfg: Path) -> None:
    assert resolve_state_path() == cfg.parent / "state.yml"


@pytest.mark.parametrize(
    ("config_name", "state_name"),
    [
        ("config.yml", "state.yml"),
        ("a.yml", "a.state.yml"),
        ("work.yaml", "work.state.yml"),
        ("untaped", "untaped.state.yml"),
        ("state.yml", "state.state.yml"),
    ],
)
def test_state_path_is_derived_from_the_config_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_name: str, state_name: str
) -> None:
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / config_name))
    assert resolve_state_path() == tmp_path / state_name


def test_sibling_configs_keep_separate_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = tmp_path / "a.yml", tmp_path / "b.yml"
    a.write_text("demo:\n  items:\n    - name: from-a\n")
    b.write_text("demo:\n  items:\n    - name: from-b\n")
    items = StateCollection("demo", "items")
    monkeypatch.setenv("UNTAPED_CONFIG", str(a))
    items.upsert({"name": "a2"})
    monkeypatch.setenv("UNTAPED_CONFIG", str(b))
    assert items.entries() == [{"name": "from-b"}]
    items.upsert({"name": "b2"})
    assert items.entries() == [{"name": "from-b"}, {"name": "b2"}]
    assert "demo" not in (yaml.safe_load(b.read_text()) or {})
    monkeypatch.setenv("UNTAPED_CONFIG", str(a))
    assert items.entries() == [{"name": "from-a"}, {"name": "a2"}]


def test_unwritable_state_location_is_a_config_error(
    cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    target = blocker / "state.yml"
    monkeypatch.setenv("UNTAPED_STATE", str(target))
    with pytest.raises(ConfigError, match=f"could not create .*{blocker}"):
        StateCollection("demo", "items").upsert({"name": "a"})


def test_state_path_env_override(
    cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_STATE", str(tmp_path / "elsewhere" / "s.yml"))
    assert resolve_state_path() == tmp_path / "elsewhere" / "s.yml"
    StateCollection("demo", "items").upsert({"name": "a"})
    assert (tmp_path / "elsewhere" / "s.yml").is_file()
    assert not (cfg.parent / "state.yml").exists()


def test_state_path_must_not_be_the_config_file(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_STATE", str(cfg))
    with pytest.raises(ConfigError, match="must not be the config file"):
        resolve_state_path()


@pytest.mark.parametrize("section", ["profiles", "active", "http", "ui", "log_level"])
def test_reserved_sections_are_never_state(cfg: Path, section: str) -> None:
    cfg.write_text("profiles:\n  default: {}\nactive: default\n")
    with pytest.raises(ConfigError, match="reserved"):
        mutate_tool_state(section, lambda state: state.update(x=1))
    with pytest.raises(ConfigError, match="reserved"):
        read_tool_state(section)
    assert yaml.safe_load(cfg.read_text()) == {"profiles": {"default": {}}, "active": "default"}


@pytest.mark.parametrize("section", ["profiles", "active"])
def test_reserved_names_cannot_register_state(cfg: Path, section: str) -> None:
    with pytest.raises(ConfigError, match="reserved"):
        register_state_settings(section, DemoState)


# ── legacy reads ─────────────────────────────────────────────────────────────


def test_state_file_wins_over_legacy_copy(cfg: Path) -> None:
    cfg.write_text(LEGACY)
    (cfg.parent / "state.yml").write_text("demo:\n  items:\n    - name: fresh\n")
    assert StateCollection("demo", "items").entries() == [{"name": "fresh"}]


def test_legacy_state_is_read_with_one_warning(
    cfg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg.write_text(LEGACY)
    items = StateCollection("demo", "items")
    assert items.entries() == [{"name": "alpha"}]
    assert items.entries() == [{"name": "alpha"}]
    err = capsys.readouterr().err
    assert err.count("warning: capability state section 'demo'") == 1
    assert str(cfg) in err
    assert str(cfg.parent / "state.yml") in err
    assert cfg.read_text() == LEGACY  # a read never migrates


def test_settings_splice_reads_state_file(cfg: Path) -> None:
    register_profile_settings("demo", DemoProfile)
    register_state_settings("demo", DemoState)
    cfg.write_text("profiles:\n  default:\n    demo:\n      url: https://x\n")
    (cfg.parent / "state.yml").write_text("demo:\n  items:\n    - name: s\n")
    section = get_settings().demo  # type: ignore[attr-defined]
    assert section.url == "https://x"  # type: ignore[attr-defined]
    assert section.items == [{"name": "s"}]  # type: ignore[attr-defined]


def test_settings_splice_falls_back_to_legacy(cfg: Path) -> None:
    register_profile_settings("demo", DemoProfile)
    register_state_settings("demo", DemoState)
    cfg.write_text(LEGACY)
    assert get_settings().demo.items == [{"name": "alpha"}]  # type: ignore[attr-defined]


def test_invalid_state_error_names_the_state_file(cfg: Path) -> None:
    register_profile_settings("demo", DemoProfile)
    register_state_settings("demo", DemoState)
    state_file = cfg.parent / "state.yml"
    state_file.write_text("demo:\n  items: nope\n")
    with pytest.raises(ConfigError, match=f"invalid state section 'demo' in {state_file}"):
        get_settings()


def test_broken_state_file_does_not_block_settings_only_sections(cfg: Path) -> None:
    register_profile_settings("demo", DemoProfile)
    register_state_settings("demo", DemoState)
    register_profile_settings("other", DemoProfile)
    cfg.write_text("profiles:\n  default:\n    other:\n      url: https://o\n")
    (cfg.parent / "state.yml").write_text("demo: [unclosed\n")
    assert get_config_section("other", DemoProfile).url == "https://o"


# ── migration on write ───────────────────────────────────────────────────────


def test_first_write_moves_legacy_section(cfg: Path) -> None:
    cfg.write_text(LEGACY)
    StateCollection("demo", "items").upsert({"name": "beta"})
    assert _state(cfg) == {"demo": {"items": [{"name": "alpha"}, {"name": "beta"}]}}
    text = cfg.read_text()
    assert "demo" not in yaml.safe_load(text)
    assert "# my config\n" in text
    assert "url: https://example.com   # keep me" in text


def test_noop_write_does_not_migrate(cfg: Path) -> None:
    cfg.write_text(LEGACY)
    assert StateCollection("demo", "items").remove("ghost") is False
    assert cfg.read_text() == LEGACY
    assert not (cfg.parent / "state.yml").exists()


def test_emptying_legacy_section_removes_it_everywhere(cfg: Path) -> None:
    cfg.write_text(LEGACY)
    assert StateCollection("demo", "items").remove("alpha") is True
    assert "demo" not in yaml.safe_load(cfg.read_text())
    assert "demo" not in _state(cfg)
    assert StateCollection("demo", "items").entries() == []


def test_write_with_both_copies_leaves_config_alone(cfg: Path) -> None:
    cfg.write_text(LEGACY)
    (cfg.parent / "state.yml").write_text("demo:\n  items:\n    - name: fresh\n")
    StateCollection("demo", "items").upsert({"name": "beta"})
    assert cfg.read_text() == LEGACY
    assert _state(cfg)["demo"]["items"] == [{"name": "fresh"}, {"name": "beta"}]


def test_malformed_legacy_section_is_not_overwritten(cfg: Path) -> None:
    cfg.write_text("demo: [1, 2]\n")
    with pytest.raises(ConfigError, match="must be a mapping"):
        mutate_tool_state("demo", lambda state: state.update(items=[]))
    assert cfg.read_text() == "demo: [1, 2]\n"
    assert not (cfg.parent / "state.yml").exists()


def test_malformed_state_section_is_not_overwritten(cfg: Path) -> None:
    state_file = cfg.parent / "state.yml"
    state_file.write_text("demo: [1, 2]\n")
    with pytest.raises(ConfigError, match="must be a mapping"):
        mutate_tool_state("demo", lambda state: state.update(items=[]))
    assert state_file.read_text() == "demo: [1, 2]\n"


def test_failed_state_write_leaves_config_untouched(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg.write_text(LEGACY)
    real = config_file.write_config_dict

    def _fail_state(data: dict[str, Any], path: Path | None = None) -> None:
        if path is not None and path.name == "state.yml":
            raise OSError(28, "No space left on device")
        real(data, path)

    monkeypatch.setattr(config_file, "write_config_dict", _fail_state)
    with pytest.raises(OSError, match="No space"):
        StateCollection("demo", "items").upsert({"name": "beta"})
    assert cfg.read_text() == LEGACY


def test_failed_legacy_removal_warns_and_keeps_state(
    cfg: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg.write_text(LEGACY)
    real = config_file.write_config_dict

    def _fail_config(data: dict[str, Any], path: Path | None = None) -> None:
        if path == cfg:
            raise OSError(30, "Read-only file system")
        real(data, path)

    monkeypatch.setattr(config_file, "write_config_dict", _fail_config)
    StateCollection("demo", "items").upsert({"name": "beta"})
    assert cfg.read_text() == LEGACY
    assert [row["name"] for row in _state(cfg)["demo"]["items"]] == ["alpha", "beta"]
    assert "could not be removed" in capsys.readouterr().err
    assert StateCollection("demo", "items").entries() == [{"name": "alpha"}, {"name": "beta"}]


def test_failed_legacy_removal_of_emptied_section_keeps_placeholder(
    cfg: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg.write_text(LEGACY)
    real = config_file.write_config_dict

    def _fail_config(data: dict[str, Any], path: Path | None = None) -> None:
        if path == cfg:
            raise OSError(30, "Read-only file system")
        real(data, path)

    monkeypatch.setattr(config_file, "write_config_dict", _fail_config)
    items = StateCollection("demo", "items")
    assert items.remove("alpha") is True
    assert "could not be removed" in capsys.readouterr().err
    assert cfg.read_text() == LEGACY
    assert _state(cfg) == {"demo": {}}  # shadows the stale copy
    assert items.entries() == []
    items.upsert({"name": "x"})
    assert items.remove("x") is True
    assert _state(cfg) == {"demo": {}}  # still shadowing while the copy exists
    assert items.entries() == []


def test_symlinked_config_is_not_replaced(
    cfg: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = tmp_path / "dotfiles" / "config.yml"
    real.parent.mkdir()
    real.write_text(LEGACY)
    cfg.symlink_to(real)
    StateCollection("demo", "items").upsert({"name": "beta"})
    assert cfg.is_symlink()
    assert real.read_text() == LEGACY
    assert "symlink" in capsys.readouterr().err
    assert StateCollection("demo", "items").entries() == [{"name": "alpha"}, {"name": "beta"}]


def test_state_write_creates_missing_directories(
    cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a" / "b" / "state.yml"
    monkeypatch.setenv("UNTAPED_STATE", str(target))
    StateCollection("demo", "items").upsert({"name": "a"})
    assert yaml.safe_load(target.read_text()) == {"demo": {"items": [{"name": "a"}]}}
    assert target.stat().st_mode & 0o777 == 0o600
    assert not cfg.exists()


# ── settings writes never touch state ────────────────────────────────────────


def test_config_set_does_not_touch_state_file(cfg: Path) -> None:
    register_profile_settings("demo", DemoProfile)
    register_state_settings("demo", DemoState)
    state_file = cfg.parent / "state.yml"
    state_file.write_text("# state\ndemo:\n  items: []\n")
    SettingsFileRepository().set_value("demo.url", "https://new")
    assert state_file.read_text() == "# state\ndemo:\n  items: []\n"
    assert yaml.safe_load(cfg.read_text()) == {
        "profiles": {"default": {"demo": {"url": "https://new"}}}
    }


def test_config_set_leaves_legacy_state_in_place(cfg: Path) -> None:
    register_profile_settings("demo", DemoProfile)
    register_state_settings("demo", DemoState)
    cfg.write_text(LEGACY)
    SettingsFileRepository().set_value("demo.url", "https://new")
    assert yaml.safe_load(cfg.read_text())["demo"] == {"items": [{"name": "alpha"}]}
    assert not (cfg.parent / "state.yml").exists()
