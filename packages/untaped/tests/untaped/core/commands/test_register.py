"""``untaped setup register``: the one command that makes an install hold its store layouts."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

import pytest

from core_test_support.commands.support import make_plugin
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.sdk.storage import _refs as refs
from untaped.sdk.storage import data_file
from untaped.sdk.storage._refs import RefTarget
from untaped.sdk.storage.dir import Dir
from untaped.sdk.storage.store import Store
from untaped.sdk.testing import CliResult, invoke_cli, plugin_candidate

pytestmark = pytest.mark.usefixtures("home_run")

_JIRA = plugin_candidate(make_plugin("jira"))


def _acme() -> PluginCandidate:
    """Plugin ``acme`` holding its ``data`` directory at layout 3."""
    return plugin_candidate(make_plugin("acme", data=Dir(layout=3, file="data.db")))


def _cli(*args: str) -> CliResult:
    root = bootstrap.build_root_app(candidates=[_acme()])
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
    assert ref.plugins == ("acme", "untaped")  # everything it composes, core included


def _start(*command: str, plugins: tuple[str, ...] = ("acme", "jira")) -> CliResult:
    """One start of the registered install, composing ``plugins`` (acme holds its data)."""
    candidates = [
        _acme() if name == "acme" else plugin_candidate(make_plugin(name)) for name in plugins
    ]
    root = bootstrap.build_root_app(candidates=candidates)
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
    assert ref is not None and ref.plugins == ("acme", "jira", "untaped")


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
        result = _start("--version")  # never waits, never fails, says nothing
    assert result.stderr == ""
    ref = refs.this_install()
    assert ref is not None and ref.plugins == ("acme", "untaped")

    _start("--version")

    ref = refs.this_install()
    assert ref is not None and ref.plugins == ("acme", "jira", "untaped")


def test_a_command_waits_for_the_references_lock_to_record_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The command takes the waiting lock, so a prune elsewhere finishes first or sees it."""
    assert _cli().exit_code == 0
    waited: list[object] = []
    held = refs.REFS.locked

    @contextmanager
    def _waits(name: str | None = None) -> Iterator[None]:
        waited.append(name)
        with held(name):
            yield

    monkeypatch.setattr(refs.REFS, "locked", _waits)
    _start("doctor", "--help")
    ref = refs.this_install()
    assert waited == [None] and ref is not None and ref.plugins == ("acme", "jira", "untaped")


def test_a_command_still_runs_when_the_references_lock_stays_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from filelock import FileLock

    assert _cli().exit_code == 0
    monkeypatch.setattr(refs.REFS, "wait", timedelta(seconds=0.01))
    with FileLock(str(refs.REFS.lock_path())):
        result = _start("doctor", "--help")  # busy past the wait: warns, never fails
    assert "could not record this install's plugins" in result.stderr
    ref = refs.this_install()
    assert ref is not None and ref.plugins == ("acme", "untaped")
    assert "could not record" not in _start("doctor", "--help").stderr
    ref = refs.this_install()
    assert ref is not None and ref.plugins == ("acme", "jira", "untaped")


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


def test_the_busy_lock_warning_follows_the_commands_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under ``--format json`` stderr stays one JSON object per line, this warning included."""
    from filelock import FileLock

    assert _cli().exit_code == 0
    monkeypatch.setattr(refs.REFS, "wait", timedelta(seconds=0.01))
    root = bootstrap.build_root_app(candidates=[_acme(), _JIRA])
    with FileLock(str(refs.REFS.lock_path())):
        # Nothing marks settings to set up, so plan fails: its error is a JSON line too.
        result = invoke_cli(root.meta, ["setup", "plan", "--format", "json"])
    lines = [json.loads(line) for line in result.stderr.splitlines()]
    assert any("could not record this install's plugins" in str(line) for line in lines)
