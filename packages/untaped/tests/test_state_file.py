"""Capability state lives in ``state.yml``, separate from ``config.yml``.

Covers path resolution, reads and writes of ``state.yml``, and the isolation
between settings writes and state writes. A state section left at the top
level of ``config.yml`` (the pre-8.0 layout) is an unknown key: ignored.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

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
    (tmp_path / "a.state.yml").write_text("demo:\n  items:\n    - name: from-a\n")
    (tmp_path / "b.state.yml").write_text("demo:\n  items:\n    - name: from-b\n")
    items = StateCollection("demo", "items")
    monkeypatch.setenv("UNTAPED_CONFIG", str(a))
    items.upsert({"name": "a2"})
    monkeypatch.setenv("UNTAPED_CONFIG", str(b))
    assert items.entries() == [{"name": "from-b"}]
    items.upsert({"name": "b2"})
    assert items.entries() == [{"name": "from-b"}, {"name": "b2"}]
    assert not b.exists()
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


@pytest.mark.parametrize(
    "section", ["profiles", "active", "format_version", "http", "ui", "skills"]
)
def test_reserved_sections_are_never_state(cfg: Path, section: str) -> None:
    cfg.write_text("profiles:\n  default: {}\nactive: default\n")
    with pytest.raises(ConfigError, match="reserved"):
        mutate_tool_state(section, lambda state: state.update(x=1))
    with pytest.raises(ConfigError, match="reserved"):
        read_tool_state(section)
    assert yaml.safe_load(cfg.read_text()) == {"profiles": {"default": {}}, "active": "default"}


@pytest.mark.parametrize("section", ["profiles", "active", "format_version"])
def test_reserved_names_cannot_register_state(cfg: Path, section: str) -> None:
    with pytest.raises(ConfigError, match="reserved"):
        register_state_settings(section, DemoState)


# ── the pre-8.0 layout is ignored ────────────────────────────────────────────


def test_state_left_in_config_is_not_read(cfg: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg.write_text(LEGACY)
    assert StateCollection("demo", "items").entries() == []
    assert read_tool_state("demo") == {}
    assert capsys.readouterr().err == ""


def test_state_file_is_used_even_with_a_copy_in_config(cfg: Path) -> None:
    cfg.write_text(LEGACY)
    (cfg.parent / "state.yml").write_text("demo:\n  items:\n    - name: fresh\n")
    assert StateCollection("demo", "items").entries() == [{"name": "fresh"}]


def test_settings_splice_ignores_state_left_in_config(cfg: Path) -> None:
    register_profile_settings("demo", DemoProfile)
    register_state_settings("demo", DemoState)
    cfg.write_text(LEGACY)
    assert get_settings().demo.items == []  # type: ignore[attr-defined]


def test_state_write_never_touches_config(cfg: Path) -> None:
    cfg.write_text(LEGACY)
    StateCollection("demo", "items").upsert({"name": "beta"})
    assert cfg.read_text() == LEGACY
    assert _state(cfg) == {"demo": {"items": [{"name": "beta"}]}}
    assert StateCollection("demo", "items").remove("beta") is True
    assert cfg.read_text() == LEGACY
    assert _state(cfg) == {}


def test_settings_splice_reads_state_file(cfg: Path) -> None:
    register_profile_settings("demo", DemoProfile)
    register_state_settings("demo", DemoState)
    cfg.write_text("profiles:\n  default:\n    demo:\n      url: https://x\n")
    (cfg.parent / "state.yml").write_text("demo:\n  items:\n    - name: s\n")
    section = get_settings().demo  # type: ignore[attr-defined]
    assert section.url == "https://x"  # type: ignore[attr-defined]
    assert section.items == [{"name": "s"}]  # type: ignore[attr-defined]


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


# ── writes ───────────────────────────────────────────────────────────────────


def test_malformed_state_section_is_not_overwritten(cfg: Path) -> None:
    state_file = cfg.parent / "state.yml"
    state_file.write_text("demo: [1, 2]\n")
    with pytest.raises(ConfigError, match="must be a mapping"):
        mutate_tool_state("demo", lambda state: state.update(items=[]))
    assert state_file.read_text() == "demo: [1, 2]\n"


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
