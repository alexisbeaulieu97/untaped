"""Tests for the SDK safe shared-config surface (ensure_config + tool state)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from untaped.config_file import (
    ensure_config,
    mutate_tool_state,
    read_config_dict,
    read_tool_state,
)


def test_ensure_config_creates_file_when_absent(tmp_path: Path) -> None:
    cfg = tmp_path / "nested" / "config.yml"
    result = ensure_config(cfg)
    assert result == cfg
    assert cfg.is_file()
    assert read_config_dict(cfg) == {}


def test_ensure_config_is_noop_when_present(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("github:\n  token: keep\n", encoding="utf-8")
    ensure_config(cfg)
    assert cfg.read_text(encoding="utf-8") == "github:\n  token: keep\n"


def test_mutate_tool_state_creates_section(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"

    def _set(state: dict[str, Any]) -> None:
        state["aliases"] = {"a": "b"}

    mutate_tool_state("ansible", _set, path=cfg)
    assert read_tool_state("ansible", path=cfg) == {"aliases": {"a": "b"}}


def test_mutate_tool_state_preserves_foreign_section(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("github:\n  token: secret\n", encoding="utf-8")

    def _set(state: dict[str, Any]) -> None:
        state["sources"] = []

    mutate_tool_state("ansible", _set, path=cfg)
    data = read_config_dict(cfg)
    assert data["github"] == {"token": "secret"}
    assert data["ansible"] == {"sources": []}


def test_mutate_tool_state_preserves_unknown_same_section_key(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("ansible:\n  future_key: keep\n", encoding="utf-8")

    def _set(state: dict[str, Any]) -> None:
        state["aliases"] = {"x": "y"}

    mutate_tool_state("ansible", _set, path=cfg)
    assert read_tool_state("ansible", path=cfg) == {"future_key": "keep", "aliases": {"x": "y"}}


def test_mutate_tool_state_removes_emptied_section(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text("ansible:\n  aliases:\n    a: b\n", encoding="utf-8")

    def _clear(state: dict[str, Any]) -> None:
        state.clear()

    mutate_tool_state("ansible", _clear, path=cfg)
    assert "ansible" not in read_config_dict(cfg)


def test_read_tool_state_absent_returns_empty(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yml"
    assert read_tool_state("ansible", path=cfg) == {}


def test_safe_config_surface_exported_from_api() -> None:
    from untaped.api import ensure_config, mutate_tool_state, read_tool_state  # noqa: F401
