"""``untaped setup register``: the one command that makes an install hold its store layouts."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
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
    assert ref.plugins == ("acme",)  # the plugins it composes, whose folders it uses


def _start(*command: str, plugins: tuple[str, ...] = ("acme", "jira")) -> CliResult:
    """One start of the registered install, composing ``plugins`` (acme holds STORE)."""
    specs = [PluginSpec(name=name, stores=(STORE,) if name == "acme" else ()) for name in plugins]
    root = bootstrap.build_root_app(candidates=[plugin_candidate(spec) for spec in specs])
    result = invoke_cli(root.meta, list(command))
    assert result.exit_code == 0, result.output
    return result


@pytest.mark.parametrize("command", [["doctor", "--help"], ["--version"]])
def test_a_start_records_a_plugin_added_since(
    command: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A registered install gains jira and runs no command that opens a store."""
    assert _cli().exit_code == 0
    opened: list[object] = []
    monkeypatch.setattr(Store, "open", lambda self: opened.append(self))

    _start(*command)

    assert opened == []
    ref = refs.this_install()
    assert ref is not None and ref.plugins == ("acme", "jira")


def test_a_start_where_nothing_changed_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _cli().exit_code == 0
    ref = refs.this_install()
    assert ref is not None
    before = (ref.file.read_bytes(), ref.file.stat().st_mtime_ns)
    monkeypatch.setattr(refs.REFS, "try_locked", _no_lock)  # no lock taken either

    _start("--version", plugins=("acme",))

    assert (ref.file.read_bytes(), ref.file.stat().st_mtime_ns) == before


def test_a_start_that_meets_a_busy_lock_leaves_the_refresh_to_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _cli().exit_code == 0
    with monkeypatch.context() as busy:
        busy.setattr(refs.REFS, "try_locked", _busy)
        _start("--version")  # never waits, never fails
    ref = refs.this_install()
    assert ref is not None and ref.plugins == ("acme",)

    _start("--version")

    ref = refs.this_install()
    assert ref is not None and ref.plugins == ("acme", "jira")


@contextmanager
def _busy(_name: str | None = None) -> Iterator[bool]:
    yield False  # another process holds the references' lock


def _no_lock(_name: str | None = None) -> object:
    raise AssertionError("a start where nothing changed took the references' lock")


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
