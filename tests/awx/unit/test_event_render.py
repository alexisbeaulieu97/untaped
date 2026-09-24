"""Unit tests for :mod:`untaped.capabilities.awx.cli.event_render`.

Pins the plain text of :func:`render_event_text` (byte-stable ``--track``
output) and the style of each runner verdict so the colours don't drift.
"""

from __future__ import annotations

import pytest
from rich.text import Text

from untaped.capabilities.awx.cli.event_render import render_event_text
from untaped.capabilities.awx.domain import JobEvent


def _ev(event: str, **fields: object) -> JobEvent:
    return JobEvent(counter=1, event=event, **fields)


def _spans(text: Text) -> list[tuple[str, str]]:
    """``(substring, style)`` for every styled span, plus the whole-line style."""
    plain = text.plain
    spans = [(plain[span.start : span.end], str(span.style)) for span in text.spans]
    return [(plain, str(text.style)), *spans] if text.style else spans


@pytest.mark.parametrize(
    ("event", "plain", "styled"),
    [
        (
            _ev("playbook_on_play_start", play="Deploy"),
            "PLAY [Deploy]",
            ("PLAY [Deploy]", "bold cyan"),
        ),
        (
            _ev("playbook_on_task_start", task="install"),
            "TASK [install]",
            ("TASK [install]", "bold blue"),
        ),
        (_ev("playbook_on_stats"), "PLAY RECAP", ("PLAY RECAP", "bold")),
        (_ev("runner_on_ok", host=5, host_name="web-01"), "  ok: web-01", ("ok", "green")),
        (_ev("runner_on_changed", host_name="web-01"), "  changed: web-01", ("changed", "yellow")),
        (_ev("runner_on_failed", host=42), "  failed: 42", ("failed", "bold red")),
        (
            _ev("runner_on_unreachable", host_name="db"),
            "  unreachable: db",
            ("unreachable", "bold red"),
        ),
        (_ev("runner_on_skipped", host_name="web-01"), "  skipped: web-01", ("skipped", "cyan")),
        (_ev("runner_on_ok"), "  ok: ?", ("ok", "green")),
        # success output never carries the host's stdout
        (
            _ev("runner_on_ok", host_name="web-01", stdout="ok: [web-01]"),
            "  ok: web-01",
            ("ok", "green"),
        ),
        # unknown events fall back to their name and whatever context they carry
        (_ev("custom", host_name="web-01", task="t"), "custom host=web-01 task=t", None),
        (_ev(""), "#1", None),
    ],
)
def test_render_event_text(event: JobEvent, plain: str, styled: tuple[str, str] | None) -> None:
    text = render_event_text(event)
    assert text.plain == plain
    if styled is not None:
        assert styled in _spans(text)


def test_render_event_failure_shows_the_reason_from_stdout() -> None:
    stdout = '\x1b[0;31mfatal: [web-01]: FAILED! => {"msg": "no package foo"}\x1b[0m\r\n'
    line = render_event_text(_ev("runner_on_failed", host_name="web-01", stdout=stdout))
    assert line.plain == (
        '  failed: web-01\n    fatal: [web-01]: FAILED! => {"msg": "no package foo"}'
    )


def test_render_event_failure_reason_is_capped_and_prefixed() -> None:
    stdout = "\n".join(f"line {i}" for i in range(15))
    line = render_event_text(
        _ev("runner_on_unreachable", host_name="db", stdout=stdout), prefix="deploy"
    ).plain.splitlines()
    assert line[0] == "[deploy]   unreachable: db"
    assert line[1] == "[deploy]     line 0"
    assert line[-1] == "[deploy]     … 5 more lines (see jobs events)"
    assert len(line) == 12


def test_render_event_text_with_prefix_prepends_bracketed_name() -> None:
    """Concurrent multi-template ``--track`` output stays disambiguable."""
    text = render_event_text(_ev("playbook_on_play_start", play="X"), prefix="deploy")
    assert text.plain == "[deploy] PLAY [X]"
    assert ("[deploy] ", "dim cyan") in _spans(text)
