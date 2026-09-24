"""Behavioural tests for the shared ``batch_apply`` helper."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

import pytest

from untaped.batch import BatchOutcome, batch_apply, finish
from untaped.errors import HttpError, UsageError
from untaped.prompts import reset_terminal_override, set_terminal_override
from untaped.testing import ScriptedPromptBackend, TtyStringIO
from untaped.ui import UiContext


def _describe(item: str) -> dict[str, object]:
    return {"id": item, "name": f"name-{item}"}


def _recorder() -> tuple[Callable[[str], str], list[str]]:
    """An action that records the items it was called with."""
    calls: list[str] = []

    def action(item: str) -> str:
        calls.append(item)
        return f"done-{item}"

    return action, calls


def _ui(*, interactive: bool, confirms: list[bool] | None = None) -> UiContext:
    return UiContext(
        stdin=TtyStringIO() if interactive else io.StringIO(),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        prompt_backend=ScriptedPromptBackend(confirms=confirms or []),
    )


def _run(*, interactive: bool, confirms: list[bool] | None = None, **kwargs: Any) -> Any:
    kwargs.setdefault("verb", "delete")
    kwargs.setdefault("noun", "Widget")
    kwargs.setdefault("label", lambda item: item)
    kwargs.setdefault("describe", _describe)
    kwargs.setdefault("destructive", True)
    kwargs.setdefault("ui", _ui(interactive=interactive, confirms=confirms))
    return batch_apply(**kwargs)


def test_interactive_destructive_previews_and_confirms(capsys: pytest.CaptureFixture[str]) -> None:
    action, calls = _recorder()
    ui = _ui(interactive=True, confirms=[True])
    outcome = _run(interactive=True, items=["a", "b"], action=action, ui=ui)

    captured = capsys.readouterr()
    assert "About to delete 2 Widgets:" in captured.err
    assert "name-a" in captured.err and "name-b" in captured.err
    assert captured.out == ""  # preview/progress stay off stdout
    assert ui.prompt_backend.calls == [("confirm", "Continue?")]
    assert calls == ["a", "b"]
    assert outcome.failed == 0
    assert outcome.results == [("a", "done-a"), ("b", "done-b")]


def test_interactive_destructive_can_skip_generic_preview(
    capsys: pytest.CaptureFixture[str],
) -> None:
    action, calls = _recorder()
    ui = _ui(interactive=True, confirms=[True])
    outcome = _run(
        interactive=True,
        items=["a", "b"],
        action=action,
        ui=ui,
        render_generic_preview=False,
    )

    captured = capsys.readouterr()
    assert "About to delete" not in captured.err
    assert "name-a" not in captured.err
    assert "name-b" not in captured.err
    assert captured.out == ""
    assert ui.prompt_backend.calls == [("confirm", "Continue?")]
    assert calls == ["a", "b"]
    assert outcome.failed == 0
    assert outcome.results == [("a", "done-a"), ("b", "done-b")]


def test_decline_runs_no_action() -> None:
    action, calls = _recorder()
    outcome = _run(interactive=True, confirms=[False], items=["a", "b"], action=action)

    assert calls == []
    assert outcome.results == []
    assert outcome.cancelled is True
    assert outcome.total == 2
    assert len(outcome.planned_rows) == 2


def test_finish_exits_one_with_standard_line_on_decline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = _run(interactive=True, confirms=[False], items=["a"], action=lambda item: item)

    with pytest.raises(SystemExit) as exc_info:
        finish(outcome)

    assert exc_info.value.code == 1
    assert capsys.readouterr().err.endswith("cancelled; no changes made\n")


@pytest.mark.parametrize(
    ("result", "predicate_hit", "code"),
    [
        (BatchOutcome(results=[("a", "a")], failed=1, planned_rows=[{}, {}]), False, 1),
        (BatchOutcome(results=[("a", "a")], failed=0, planned_rows=[{}]), False, None),
        (True, False, 1),
        (False, False, None),
        # A predicate hit exits 3 only when nothing failed.
        (False, True, 3),
        (True, True, 1),
    ],
    ids=["partial-failure", "success", "failed", "ok", "predicate-hit", "failed-and-hit"],
)
def test_finish_exit_codes(result: Any, predicate_hit: bool, code: int | None) -> None:
    if code is None:
        finish(result, predicate_hit=predicate_hit)
        return
    with pytest.raises(SystemExit) as excinfo:
        finish(result, predicate_hit=predicate_hit)
    assert excinfo.value.code == code


def test_piped_stdin_confirms_on_the_controlling_terminal() -> None:
    """Piped stdin carries data, so the prompt goes to the controlling terminal."""
    action, calls = _recorder()
    ui = _ui(interactive=False, confirms=[True])
    token = set_terminal_override(TtyStringIO)
    try:
        outcome = _run(interactive=False, items=["a"], action=action, ui=ui)
    finally:
        reset_terminal_override(token)

    assert ui.prompt_backend.calls == [("confirm", "Continue?")]
    assert calls == ["a"]
    assert outcome.cancelled is False
    assert not isinstance(ui.stdin, TtyStringIO)  # the data stream is restored


def test_assume_yes_skips_gate_and_preview(
    capsys: pytest.CaptureFixture[str],
) -> None:
    action, calls = _recorder()
    ui = _ui(interactive=True)
    outcome = _run(interactive=True, items=["a", "b"], action=action, ui=ui, assume_yes=True)

    assert "About to delete" not in capsys.readouterr().err
    assert ui.prompt_backend.calls == []
    assert calls == ["a", "b"]
    assert outcome.failed == 0


def test_assume_yes_skips_generic_preview_even_when_enabled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    action, calls = _recorder()
    ui = _ui(interactive=True)
    outcome = _run(
        interactive=True,
        items=["a", "b"],
        action=action,
        ui=ui,
        assume_yes=True,
        render_generic_preview=True,
    )

    assert "About to delete" not in capsys.readouterr().err
    assert ui.prompt_backend.calls == []
    assert calls == ["a", "b"]
    assert outcome.failed == 0


def test_benign_verb_skips_gate() -> None:
    action, calls = _recorder()
    # Non-interactive + no --yes would refuse a destructive verb; benign runs.
    outcome = _run(
        interactive=False,
        items=["a", "b"],
        action=action,
        destructive=False,
    )

    assert calls == ["a", "b"]
    assert outcome.results == [("a", "done-a"), ("b", "done-b")]


def test_destructive_non_interactive_refuses() -> None:
    action, calls = _recorder()
    with pytest.raises(UsageError, match="delete requires --yes when not interactive"):
        _run(interactive=False, items=["a", "b"], action=action)
    assert calls == []


def test_destructive_non_interactive_refuses_before_generic_preview(
    capsys: pytest.CaptureFixture[str],
) -> None:
    action, calls = _recorder()
    with pytest.raises(UsageError, match="requires --yes when not interactive"):
        _run(
            interactive=False,
            items=["a", "b"],
            action=action,
            render_generic_preview=False,
        )
    assert calls == []
    assert "About to delete" not in capsys.readouterr().err


def test_preview_only_returns_plan_without_acting() -> None:
    action, calls = _recorder()
    ui = _ui(interactive=True)
    outcome = _run(interactive=True, items=["a", "b"], action=action, ui=ui, preview_only=True)

    assert calls == []
    assert ui.prompt_backend.calls == []
    assert outcome.planned_rows == [_describe("a"), _describe("b")]


def test_partial_failure_counts_and_continues(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def action(item: str) -> str:
        if item == "a":
            raise HttpError("boom")
        return f"done-{item}"

    ui = _ui(interactive=True, confirms=[True])
    outcome = _run(
        interactive=True,
        items=["a", "b"],
        action=action,
        label=lambda item: f"id={item}",
        ui=ui,
    )

    assert "error: id=a: boom" in ui.stderr.getvalue()  # type: ignore[attr-defined]
    assert outcome.failed == 1
    assert outcome.results == [("b", "done-b")]
    assert outcome.any_failed


def test_empty_items_is_a_noop() -> None:
    action, calls = _recorder()
    ui = _ui(interactive=True)
    outcome = _run(interactive=True, items=[], action=action, ui=ui)

    assert calls == []
    assert ui.prompt_backend.calls == []
    assert outcome.total == 0
    assert outcome.results == []
    assert outcome.planned_rows == []


def test_non_untaped_error_propagates() -> None:
    def action(item: str) -> str:
        raise ValueError("bug")

    with pytest.raises(ValueError, match="bug"):
        _run(
            interactive=False,
            items=["a"],
            action=action,
            assume_yes=True,
        )


def test_tty_authority_is_the_context_stdin_not_sys_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real-TTY process stdin must not open the gate when context stdin is a pipe."""
    monkeypatch.setattr("sys.stdin", TtyStringIO())
    ui = _ui(interactive=False)
    with pytest.raises(UsageError, match="requires --yes"):
        batch_apply(
            ["a"],
            lambda item: item,
            verb="delete",
            noun="thing",
            label=str,
            describe=lambda item: {"name": item},
            ui=ui,
            destructive=True,
        )


def test_custom_preview_replaces_generic_rows(capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[list[dict[str, object]]] = []
    ui = _ui(interactive=True, confirms=[True])
    batch_apply(
        ["a", "b"],
        lambda item: item,
        verb="delete",
        noun="thing",
        label=str,
        describe=lambda item: {"name": item},
        ui=ui,
        destructive=True,
        preview=seen.append,
    )
    assert seen == [[{"name": "a"}, {"name": "b"}]]
    assert "About to delete" not in capsys.readouterr().err


def test_failure_lines_use_the_shared_error_formatter() -> None:
    """API messages from an HttpError body are kept (same as ``report_errors``)."""

    def action(item: str) -> str:
        raise HttpError("HTTP 400", status_code=400, body='{"detail": "name taken"}')

    ui = _ui(interactive=False)
    _run(interactive=False, items=["a"], action=action, assume_yes=True, ui=ui)

    assert "error: a: HTTP 400 — name taken" in ui.stderr.getvalue()  # type: ignore[attr-defined]


def test_failure_lines_do_not_interleave_with_the_spinner() -> None:
    def action(item: str) -> str:
        raise HttpError("boom")

    stderr = TtyStringIO()
    ui = UiContext(stdin=io.StringIO(), stdout=io.StringIO(), stderr=stderr)
    _run(interactive=False, items=["a"], action=action, assume_yes=True, ui=ui)

    lines = stderr.getvalue().split("\n")
    error_line = next(line for line in lines if "error: a: boom" in line)
    # The spinner is cleared before the error prints, so the error line holds
    # nothing but the error (after the line-clearing carriage return).
    assert error_line.rsplit("\r", 1)[-1] == "error: a: boom"
