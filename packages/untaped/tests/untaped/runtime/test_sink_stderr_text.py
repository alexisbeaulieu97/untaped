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
import re
from collections.abc import Callable
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from untaped.runtime import verbose
from untaped.runtime.run import entered, start_run
from untaped.runtime.sink import Diagnostics, current_sink
from untaped.runtime.theme import BUILTIN_THEMES
from untaped.sdk._cli import (
    clamp_parallel,
    raise_usage,
    report_declined,
)
from untaped.sdk._run import Rendering
from untaped.sdk._theme import ThemeSpec
from untaped.sdk._ui import Ui, collection
from untaped.sdk.errors import ConfigError, OperationCancelledError, UntapedError
from untaped.sdk.testing import progress_clock

SNAPSHOTS = Path(__file__).parents[1] / "snapshots" / "stderr"
#: Read at import: the hermetic test environment clears ``UNTAPED_*`` variables.
_UPDATE = bool(os.environ.get("UNTAPED_UPDATE_SNAPSHOTS"))

#: A theme that colours every message role.
_COLOURED = BUILTIN_THEMES["high-contrast"]
_SYMBOLS = _COLOURED.model_copy(
    update={"symbols": {"success": "✓", "info": "i", "warning": "!", "error": "✗"}}
)


def _ui(theme: ThemeSpec | None = None) -> Ui:
    return Ui(theme=theme or _COLOURED)


def _messages(theme: ThemeSpec | None = None) -> None:
    with entered(start_run()) as run:
        run.render_with(Rendering(theme=theme or _COLOURED))
        diag = Diagnostics()
        diag.success("a success line")
        diag.info("a info line")
        diag.warning("a warning line")


def _report_error_plain() -> None:
    Diagnostics().failure(UntapedError("it broke"))


def _report_error_item() -> None:
    Diagnostics().failure(UntapedError("it broke"), item="acme/x")


def _report_error_hint() -> None:
    Diagnostics().failure(ConfigError("no token", hint="run `untaped auth login`"))


def _declined() -> None:
    report_declined(OperationCancelledError())


def _usage() -> None:
    with pytest.raises(SystemExit):
        raise_usage("--foo takes a number")


def _clamp() -> None:
    clamp_parallel(64, cap=8, policy="2 * os.cpu_count()")


def _empty_note() -> None:
    collection(_ui(), [], fmt="table", empty="No repos found.")


def _debug_line() -> None:
    with entered(start_run()) as run:
        verbose.enable(run)
        logging.getLogger("untaped.http").debug("GET %s", "https://example.test/api")


def _progress_lines() -> None:
    with progress_clock() as clock, Diagnostics().progress("Syncing repos") as handle:
        handle.update("acme/x", done=1, total=2)
        clock.advance(2)
        Diagnostics().failure(UntapedError("it broke"), item="acme/x")
        handle.update("acme/y", done=2, total=2)


def _progress_verbose() -> None:
    with progress_clock(), current_sink().progress("Syncing repos", verbose=True) as handle:
        handle.update("acme/x", done=1, total=2)
        Diagnostics().relay("a relayed line")


CASES: dict[str, Callable[[], None]] = {
    "report-error": _report_error_plain,
    "report-error-item": _report_error_item,
    "report-error-hint": _report_error_hint,
    "declined": _declined,
    "usage": _usage,
    "clamp-parallel": _clamp,
    "empty-note": _empty_note,
    "debug-line": _debug_line,
    "progress-lines": _progress_lines,
    "progress-verbose": _progress_verbose,
}
#: Cases whose text a theme colours or decorates.
THEMED = ("empty-note",)


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


def _json_lines(case: Callable[[], None]) -> list[dict[str, object]]:
    return [json.loads(line) for line in _capture(case).splitlines() if line]


@pytest.mark.parametrize("case", sorted(CASES))
def test_json_stderr_is_unchanged(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    actual = _json_lines(CASES[case])
    path = SNAPSHOTS / f"{case}.json"
    _compare(path.name, json.dumps(actual, indent=2, ensure_ascii=False) + "\n")


@pytest.mark.parametrize(
    ("snapshot", "theme", "color"),
    [
        ("messages.txt", _COLOURED, False),
        ("messages-color.txt", _COLOURED, True),
        ("messages-symbols.txt", _SYMBOLS, False),
        ("messages-symbols-color.txt", _SYMBOLS, True),
    ],
)
def test_diag_messages_keep_their_rendering(
    snapshot: str, theme: ThemeSpec, color: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the policy's theme, ``ctx.diag`` renders success, info and warning as before."""
    if color:
        monkeypatch.setenv("FORCE_COLOR", "1")

    def case() -> None:
        _messages(theme)

    expected = (SNAPSHOTS / snapshot).read_text(encoding="utf-8")
    assert _capture(case, _TtyStringIO() if color else None) == expected


def test_without_a_theme_diag_messages_print_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default theme has no symbols and no message colours: today's plain lines."""
    monkeypatch.setenv("FORCE_COLOR", "1")

    def case() -> None:
        with entered(start_run()):
            diag = Diagnostics()
            diag.success("done")
            diag.warning("careful", hint="look")
            diag.hint("next")
            diag.failure(UntapedError("it broke"))

    assert _capture(case, _TtyStringIO()) == (
        "done\nwarning: careful\nhint: look\nhint: next\nerror: it broke\n"
    )


def test_a_theme_leaves_hints_and_failures_plain() -> None:
    def case() -> None:
        with entered(start_run()) as run:
            run.render_with(Rendering(theme=_SYMBOLS))
            Diagnostics().hint("next")
            Diagnostics().failure(UntapedError("it broke"), item="acme/x")

    assert _capture(case) == "hint: next\nerror: acme/x: it broke\n"


def test_a_themed_info_block_is_never_wrapped_or_tab_expanded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preview (a diff, a tab-joined table) prints as written, colour or not."""
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "80")
    block = "name\tstate\n" + "x" * 300 + "\n\n\tindented"

    def case() -> None:
        with entered(start_run()) as run:
            run.render_with(Rendering(theme=_COLOURED))
            Diagnostics().info(block)

    coloured = _capture(case, _TtyStringIO())
    assert "\x1b[" in coloured
    assert re.sub(r"\x1b\[[0-9;]*m", "", coloured) == block + "\n"


def test_a_role_rich_cannot_parse_styles_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    theme = _COLOURED.model_copy(
        update={"color_roles": {**_COLOURED.color_roles, "info": "repr.number"}}
    )

    def case() -> None:
        with entered(start_run()) as run:
            run.render_with(Rendering(theme=theme))
            Diagnostics().info("plain")

    assert _capture(case, _TtyStringIO()) == "plain\n"
