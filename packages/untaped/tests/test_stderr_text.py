"""stderr as the helpers print it today, pinned byte for byte.

The output sink (#537) moves every stderr writer behind one writer and must
not change a byte of text output; these snapshots are the proof. Each case
runs with ``sys.stderr`` redirected and its text compared with
``snapshots/stderr/<case>.txt`` (colour off, colour forced, and a theme with
symbols where the helper is themed). Under JSON diagnostics the lines are
compared as parsed objects in ``<case>.json``.

A missing snapshot fails; run with ``UNTAPED_UPDATE_SNAPSHOTS=1`` to write it.
"""

from __future__ import annotations

import io
import json
import logging
import os
from collections.abc import Callable
from contextlib import redirect_stderr
from pathlib import Path

import pytest
from rich.text import Text

from untaped import verbose
from untaped.cli import (
    clamp_parallel,
    echo,
    raise_usage,
    report_declined,
    report_error,
)
from untaped.errors import ConfigError, OperationCancelledError, UntapedError
from untaped.messages import hint
from untaped.output.sink import Diagnostics
from untaped.theme import BUILTIN_THEMES, ThemeSpec
from untaped.ui import UiContext

SNAPSHOTS = Path(__file__).parent / "snapshots" / "stderr"
#: Read at import: the hermetic test environment clears ``UNTAPED_*`` variables.
_UPDATE = bool(os.environ.get("UNTAPED_UPDATE_SNAPSHOTS"))

#: A theme that colours every message role.
_COLOURED = BUILTIN_THEMES["high-contrast"]
_SYMBOLS = _COLOURED.model_copy(
    update={"symbols": {"success": "✓", "info": "i", "warning": "!", "error": "✗"}}
)


def _ui(theme: ThemeSpec | None = None) -> UiContext:
    return UiContext(theme=theme or _COLOURED)


def _messages(theme: ThemeSpec | None = None) -> None:
    ui = _ui(theme)
    for level in ("success", "info", "warning", "error"):
        ui.message(level, f"a {level} line")
    ui.success("done")


def _report_error_plain() -> None:
    report_error(UntapedError("it broke"))


def _report_error_item() -> None:
    report_error(UntapedError("it broke"), item="acme/x")


def _report_error_hint() -> None:
    report_error(ConfigError("no token", hint="run `untaped auth set`"))


def _echo_lines() -> None:
    echo("warning: something is off", err=True)
    echo(hint("try again"), err=True)
    echo("an info line", err=True)


def _declined() -> None:
    report_declined(OperationCancelledError())


def _usage() -> None:
    with pytest.raises(SystemExit):
        raise_usage("--foo takes a number")


def _clamp() -> None:
    clamp_parallel(64, cap=8, policy="2 * os.cpu_count()")


def _styled() -> None:
    _ui().styled(Text("[job 42] ", style="bold"), err=True, tail="ok\tdone")


def _empty_note() -> None:
    _ui().collection([], fmt="table", empty="No repos found.")


def _debug_line() -> None:
    token = verbose.enable()
    try:
        logging.getLogger("untaped.http").debug("GET %s", "https://example.test/api")
    finally:
        verbose.reset(token)


def _progress_lines() -> None:
    with _ui().progress("Syncing repos") as handle:
        handle.update("acme/x", done=1, total=2)
        Diagnostics().failure(UntapedError("it broke"), item="acme/x")
        handle.update("acme/y", done=2, total=2)


def _progress_verbose() -> None:
    with UiContext(theme=_COLOURED, verbose=True).progress("Syncing repos") as handle:
        handle.update("acme/x", done=1, total=2)
        Diagnostics().relay("a relayed line")


CASES: dict[str, Callable[[], None]] = {
    "messages": _messages,
    "report-error": _report_error_plain,
    "report-error-item": _report_error_item,
    "report-error-hint": _report_error_hint,
    "echo-lines": _echo_lines,
    "declined": _declined,
    "usage": _usage,
    "clamp-parallel": _clamp,
    "styled": _styled,
    "empty-note": _empty_note,
    "debug-line": _debug_line,
    "progress-lines": _progress_lines,
    "progress-verbose": _progress_verbose,
}
#: Cases whose text a theme colours or decorates.
THEMED = ("messages", "styled", "empty-note")


class _TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


def _capture(case: Callable[[], None], stream: io.StringIO | None = None) -> str:
    stream = stream if stream is not None else io.StringIO()
    with redirect_stderr(stream):
        case()
    return stream.getvalue()


def _compare(name: str, actual: str) -> None:
    path = SNAPSHOTS / name
    if not path.exists():
        if not _UPDATE:
            pytest.fail(f"no snapshot {path}; run with UNTAPED_UPDATE_SNAPSHOTS=1 to write it")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
    assert actual == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("case", sorted(CASES))
def test_text_stderr_is_unchanged(case: str) -> None:
    _compare(f"{case}.txt", _capture(CASES[case]))


@pytest.mark.parametrize("case", THEMED)
def test_coloured_stderr_is_unchanged(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    _compare(f"{case}-color.txt", _capture(CASES[case], _TtyStringIO()))


def test_no_color_wins_on_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    _compare("messages.txt", _capture(_messages, _TtyStringIO()))


@pytest.mark.parametrize("color", [False, True])
def test_theme_symbols_are_unchanged(color: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    if color:
        monkeypatch.setenv("FORCE_COLOR", "1")
    suffix = "-color" if color else ""
    _compare(
        f"messages-symbols{suffix}.txt",
        _capture(lambda: _messages(_SYMBOLS), _TtyStringIO() if color else None),
    )


def _json_lines(case: Callable[[], None]) -> list[dict[str, object]]:
    return [json.loads(line) for line in _capture(case).splitlines() if line]


@pytest.mark.parametrize("case", sorted(CASES))
def test_json_stderr_is_unchanged(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    actual = _json_lines(CASES[case])
    path = SNAPSHOTS / f"{case}.json"
    _compare(path.name, json.dumps(actual, indent=2, ensure_ascii=False) + "\n")
