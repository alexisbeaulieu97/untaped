"""``untaped setup register``: the one command that makes an install hold its store layouts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped import bootstrap, refs
from untaped.plugins.registry import PluginSpec
from untaped.store import Store
from untaped.testing import CliResult, invoke_cli, plugin_candidate

STORE = Store("acme", "data", base=lambda: Path("/nowhere/data.db"), layout=3)


def _cli(*args: str) -> CliResult:
    spec = PluginSpec(name="acme", stores=(STORE,))
    root = bootstrap.build_root_app(candidates=[plugin_candidate(spec)])
    return invoke_cli(root.meta, ["setup", "register", *args])


def test_register_writes_this_installs_reference_with_every_store_layout() -> None:
    result = _cli("--format", "json")

    assert result.exit_code == 0, result.output
    [outcome] = json.loads(result.stdout)
    env = refs.environment()
    assert outcome == {
        "action": "registered",
        "holder": refs.install_holder(env) if env else "",
        "environment": str(env),
        "version": outcome["version"],
        "layouts": ["acme.data@3"],
    }
    assert "registered untaped " in result.stderr and "(acme.data@3)" in result.stderr
    ref = refs.this_install()
    assert ref is not None and ref.targets == ("acme.data@3",)


def test_registering_twice_changes_nothing() -> None:
    assert _cli().exit_code == 0
    result = _cli("--format", "json")
    assert json.loads(result.stdout)[0]["action"] == "unchanged"
    assert "already registered" in result.stderr


def test_remove_unregisters_and_a_second_remove_finds_nothing() -> None:
    assert _cli().exit_code == 0
    removed = _cli("--remove", "--format", "json")
    assert json.loads(removed.stdout)[0]["action"] == "removed"
    assert refs.this_install() is None
    again = _cli("--remove", "--format", "json")
    assert json.loads(again.stdout)[0]["action"] == "gone"
    assert "not registered" in again.stderr


def test_a_source_tree_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(refs, "environment", lambda: None)
    result = _cli()
    assert result.exit_code == 4
    assert "source tree" in result.stderr
    assert refs.read_references() == []
