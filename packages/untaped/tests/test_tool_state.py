"""Low-level section mutations behind StateCollection and StateMap in state.yml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from untaped.config_file import (
    mutate_tool_state,
    read_config_dict,
    read_tool_state,
)


def test_mutate_tool_state_creates_section(tmp_path: Path) -> None:
    cfg = tmp_path / "state.yml"

    def _set(state: dict[str, Any]) -> None:
        state["aliases"] = {"a": "b"}

    mutate_tool_state("ansible", _set, path=cfg)
    assert read_tool_state("ansible", path=cfg) == {"aliases": {"a": "b"}}


def test_mutate_tool_state_preserves_foreign_section(tmp_path: Path) -> None:
    cfg = tmp_path / "state.yml"
    cfg.write_text("github:\n  token: secret\n", encoding="utf-8")

    def _set(state: dict[str, Any]) -> None:
        state["sources"] = []

    mutate_tool_state("ansible", _set, path=cfg)
    data = read_config_dict(cfg)
    assert data["github"] == {"token": "secret"}
    assert data["ansible"] == {"sources": []}


def test_mutate_tool_state_preserves_unknown_same_section_key(tmp_path: Path) -> None:
    cfg = tmp_path / "state.yml"
    cfg.write_text("ansible:\n  future_key: keep\n", encoding="utf-8")

    def _set(state: dict[str, Any]) -> None:
        state["aliases"] = {"x": "y"}

    mutate_tool_state("ansible", _set, path=cfg)
    assert read_tool_state("ansible", path=cfg) == {"future_key": "keep", "aliases": {"x": "y"}}


def test_mutate_tool_state_removes_emptied_section(tmp_path: Path) -> None:
    cfg = tmp_path / "state.yml"
    cfg.write_text("ansible:\n  aliases:\n    a: b\n", encoding="utf-8")

    def _clear(state: dict[str, Any]) -> None:
        state.clear()

    mutate_tool_state("ansible", _clear, path=cfg)
    assert "ansible" not in read_config_dict(cfg)


def test_read_tool_state_absent_returns_empty(tmp_path: Path) -> None:
    cfg = tmp_path / "state.yml"
    assert read_tool_state("ansible", path=cfg) == {}
