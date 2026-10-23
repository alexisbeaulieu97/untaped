"""Low-level section mutations behind StateCollection and StateMap in state.yml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from untaped.runtime.config_file import (
    mutate_plugin_state,
    read_config_dict,
    read_plugin_state,
)
from untaped.sdk.errors import UntapedError


def test_mutate_plugin_state_creates_section(tmp_path: Path) -> None:
    cfg = tmp_path / "state.yml"

    def _set(state: dict[str, Any]) -> None:
        state["aliases"] = {"a": "b"}

    mutate_plugin_state("ansible", _set, path=cfg)
    assert read_plugin_state("ansible", path=cfg) == {"aliases": {"a": "b"}}


def test_mutate_plugin_state_preserves_foreign_section(tmp_path: Path) -> None:
    cfg = tmp_path / "state.yml"
    cfg.write_text("github:\n  token: secret\n", encoding="utf-8")

    def _set(state: dict[str, Any]) -> None:
        state["sources"] = []

    mutate_plugin_state("ansible", _set, path=cfg)
    data = read_config_dict(cfg)
    assert data["github"] == {"token": "secret"}
    assert data["ansible"] == {"sources": []}


def test_mutate_plugin_state_preserves_unknown_same_section_key(tmp_path: Path) -> None:
    cfg = tmp_path / "state.yml"
    cfg.write_text("ansible:\n  future_key: keep\n", encoding="utf-8")

    def _set(state: dict[str, Any]) -> None:
        state["aliases"] = {"x": "y"}

    mutate_plugin_state("ansible", _set, path=cfg)
    assert read_plugin_state("ansible", path=cfg) == {"future_key": "keep", "aliases": {"x": "y"}}


def test_mutate_plugin_state_removes_emptied_section(tmp_path: Path) -> None:
    cfg = tmp_path / "state.yml"
    cfg.write_text("ansible:\n  aliases:\n    a: b\n", encoding="utf-8")

    def _clear(state: dict[str, Any]) -> None:
        state.clear()

    mutate_plugin_state("ansible", _clear, path=cfg)
    assert "ansible" not in read_config_dict(cfg)


def test_read_plugin_state_absent_returns_empty(tmp_path: Path) -> None:
    cfg = tmp_path / "state.yml"
    assert read_plugin_state("ansible", path=cfg) == {}


def test_nested_write_to_the_same_file_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    cfg = tmp_path / "state.yml"

    def _inner(state: dict[str, Any]) -> None:
        state["b"] = 2

    def _outer(state: dict[str, Any]) -> None:
        state["a"] = 1
        mutate_plugin_state("demo", _inner, path=cfg)

    with pytest.raises(UntapedError, match="inside another write") as excinfo:
        mutate_plugin_state("demo", _outer, path=cfg)
    assert str(cfg) in str(excinfo.value)
    assert not cfg.exists()
    # The guard ends with the outer write: a later write goes through.
    mutate_plugin_state("demo", _inner, path=cfg)
    assert read_plugin_state("demo", path=cfg) == {"b": 2}


def test_nested_write_to_the_same_file_through_a_symlink_is_refused(tmp_path: Path) -> None:
    cfg = tmp_path / "state.yml"
    link = tmp_path / "link.yml"
    link.symlink_to(cfg)

    def _outer(state: dict[str, Any]) -> None:
        state["a"] = 1
        mutate_plugin_state("demo", lambda inner: inner.update(b=2), path=link)

    with pytest.raises(UntapedError, match="inside another write"):
        mutate_plugin_state("demo", _outer, path=cfg)
    assert not cfg.exists()
