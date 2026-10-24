"""``untaped setup register``: the one command that makes an install hold its store layouts."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginSpec
from untaped.sdk.storage import _refs as refs
from untaped.sdk.storage import data_file
from untaped.sdk.storage._refs import RefTarget
from untaped.sdk.storage.store import Store
from untaped.sdk.testing import CliResult, invoke_cli, plugin_candidate

pytestmark = pytest.mark.usefixtures("home_run")

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
        "holder": refs.install_holder(env).model_dump() if env else None,
        "environment": str(env),
        "version": outcome["version"],
        "targets": [{"plugin": "acme", "store": "data", "layout": 3}],
    }
    assert "registered untaped " in result.stderr and "(acme.data layout 3)" in result.stderr
    ref = refs.this_install()
    assert ref is not None and ref.targets == (RefTarget(plugin="acme", store="data", layout=3),)


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
    [gone] = json.loads(again.stdout)
    assert gone["action"] == "gone" and "holder" not in gone
    assert "not registered" in again.stderr


def test_a_source_tree_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(refs, "environment", lambda: None)
    result = _cli()
    assert result.exit_code == 4
    assert "source tree" in result.stderr
    assert refs.read_references() == []


def test_a_reference_that_cannot_be_written_is_an_error_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(data_file, "atomic_write", refuse)
    result = _cli()
    assert result.exit_code == 4, result.output  # a config error
    assert "error: could not write " in result.stderr and "Permission denied" in result.stderr
    assert "Traceback" not in result.output


def test_a_busy_references_lock_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    from filelock import FileLock

    monkeypatch.setattr(refs.REFS, "wait", timedelta(seconds=0.01))
    lock = refs.REFS.lock_path()
    lock.parent.mkdir(parents=True)
    with FileLock(str(lock)):
        result = _cli("--remove")
    assert "untaped.refs is busy (another untaped process)" in result.stderr, result.output
