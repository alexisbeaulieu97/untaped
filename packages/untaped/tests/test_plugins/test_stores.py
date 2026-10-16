"""``PluginSpec.stores``: what composition accepts and what it quarantines."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_plugins.plugin_harness import make_candidate, make_shell
from untaped.errors import ConfigError
from untaped.plugins.registry import PluginSpec, compose
from untaped.store import Store

DATA = Store("hello", "data", base=lambda: Path("data.db"))


def _reason(spec: PluginSpec) -> tuple[str, str]:
    (record,) = compose(make_shell(), [make_candidate(spec)]).quarantine
    return record.reason, record.detail


def test_a_plugin_composes_its_stores() -> None:
    result = compose(make_shell(), [make_candidate(PluginSpec(name="hello", stores=[DATA]))])  # type: ignore[arg-type]
    assert result.quarantine == ()
    assert result.plugins[0].spec.stores == (DATA,)


def test_a_store_of_another_plugin_is_quarantined() -> None:
    other = Store("other", "data", base=lambda: Path("x.db"))
    reason, detail = _reason(PluginSpec(name="hello", stores=(other,)))
    assert reason == "foreign-store"
    assert "'other.data'" in detail


def test_a_store_declared_twice_is_quarantined() -> None:
    again = Store("hello", "data", base=lambda: Path("y.db"), layout=2)
    reason, detail = _reason(PluginSpec(name="hello", stores=(DATA, again)))
    assert reason == "duplicate-store"
    assert "'data' twice" in detail


def test_stores_holds_only_stores() -> None:
    with pytest.raises(ConfigError, match="not a Store"):
        PluginSpec(name="hello", stores=("data",))  # type: ignore[arg-type]
