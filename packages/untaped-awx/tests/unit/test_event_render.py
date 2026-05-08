"""Unit tests for :mod:`untaped_awx.cli._event_render`.

Two surfaces:

- :func:`render_event` — plain string, byte-stable.
- :func:`render_event_text` — :class:`rich.text.Text` carrying status
  styles. We assert the *style* attached to each runner verdict so the
  colour mapping doesn't drift silently.
"""

from __future__ import annotations

from rich.text import Text
from untaped_awx.cli._event_render import render_event, render_event_text
from untaped_awx.domain import JobEvent


def _ev(event: str, **fields: object) -> JobEvent:
    return JobEvent(counter=1, event=event, **fields)  # type: ignore[arg-type]


# --- plain string surface ---------------------------------------------------


def test_render_event_play_start() -> None:
    assert render_event(_ev("playbook_on_play_start", play="Deploy")) == "PLAY [Deploy]"


def test_render_event_task_start() -> None:
    assert render_event(_ev("playbook_on_task_start", task="install")) == "TASK [install]"


def test_render_event_runner_ok_uses_host_name() -> None:
    line = render_event(_ev("runner_on_ok", host=5, host_name="web-01"))
    assert line == "  ok: web-01"


def test_render_event_runner_failed_falls_back_to_host_id_when_name_missing() -> None:
    line = render_event(_ev("runner_on_failed", host=42))
    assert line == "  failed: 42"


def test_render_event_runner_unknown_host_renders_question_mark() -> None:
    line = render_event(_ev("runner_on_ok"))
    assert line == "  ok: ?"


# --- styled (Rich Text) surface --------------------------------------------


def _spans(text: Text) -> list[tuple[str, str]]:
    """Return ``(substring, style)`` pairs for every styled span in ``text``."""
    plain = text.plain
    return [(plain[span.start : span.end], str(span.style)) for span in text.spans]


def test_render_event_text_play_start_uses_play_style() -> None:
    text = render_event_text(_ev("playbook_on_play_start", play="Deploy"))
    assert text.plain == "PLAY [Deploy]"
    assert str(text.style) == "bold cyan"


def test_render_event_text_task_start_uses_task_style() -> None:
    text = render_event_text(_ev("playbook_on_task_start", task="install"))
    assert text.plain == "TASK [install]"
    assert str(text.style) == "bold blue"


def test_render_event_text_runner_ok_styles_verdict_green() -> None:
    text = render_event_text(_ev("runner_on_ok", host=5, host_name="web-01"))
    assert text.plain == "  ok: web-01"
    assert ("ok", "green") in _spans(text)


def test_render_event_text_runner_changed_styles_verdict_yellow() -> None:
    text = render_event_text(_ev("runner_on_changed", host=5, host_name="web-01"))
    assert ("changed", "yellow") in _spans(text)


def test_render_event_text_runner_failed_styles_verdict_bold_red() -> None:
    text = render_event_text(_ev("runner_on_failed", host=6, host_name="api-01"))
    assert ("failed", "bold red") in _spans(text)


def test_render_event_text_runner_unreachable_styles_verdict_bold_red() -> None:
    text = render_event_text(_ev("runner_on_unreachable", host=6, host_name="api-01"))
    assert ("unreachable", "bold red") in _spans(text)


def test_render_event_text_runner_skipped_styles_verdict_cyan() -> None:
    text = render_event_text(_ev("runner_on_skipped", host=5, host_name="web-01"))
    assert ("skipped", "cyan") in _spans(text)


def test_render_event_text_recap_uses_recap_style() -> None:
    text = render_event_text(_ev("playbook_on_stats"))
    assert text.plain == "PLAY RECAP"
    assert str(text.style) == "bold"


# --- prefix support (parallel multi-template launch) -----------------------


def test_render_event_text_no_prefix_renders_unchanged() -> None:
    """``prefix=""`` (default) keeps the output identical to a no-prefix
    call — every existing caller continues to work without changes.
    """
    base = render_event_text(_ev("playbook_on_play_start", play="Deploy"))
    same = render_event_text(_ev("playbook_on_play_start", play="Deploy"), prefix="")
    assert same.plain == base.plain
    assert _spans(same) == _spans(base)


def test_render_event_text_with_prefix_prepends_bracketed_name() -> None:
    """``prefix="deploy"`` prepends ``[deploy] `` (dim cyan) to whatever
    the renderer would have emitted, so concurrent multi-template
    output stays disambiguable on stderr.
    """
    text = render_event_text(_ev("playbook_on_play_start", play="X"), prefix="deploy")
    assert text.plain == "[deploy] PLAY [X]"
    assert ("[deploy] ", "dim cyan") in _spans(text)


def test_render_event_with_prefix_returns_plain_string() -> None:
    """``render_event`` mirrors the prefix wiring so plain-text consumers
    (tests, ``--format raw``) see the same disambiguation.
    """
    line = render_event(_ev("runner_on_ok", host=5, host_name="web-01"), prefix="x")
    assert line == "[x] " + "  ok: web-01"
