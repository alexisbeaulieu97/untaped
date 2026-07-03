"""Behavioural tests for the shared ``batch_apply`` helper."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

import pytest

from untaped.batch import BatchOutcome, batch_apply, finish
from untaped.errors import ConfigError, HttpError
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
    assert "About to delete 2 Widget(s):" in captured.err
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
    assert outcome.total == 2
    assert len(outcome.planned_rows) == 2


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
    with pytest.raises(ConfigError, match="requires --yes when stdin is not interactive"):
        _run(interactive=False, items=["a", "b"], action=action)
    assert calls == []


def test_destructive_non_interactive_refuses_before_generic_preview(
    capsys: pytest.CaptureFixture[str],
) -> None:
    action, calls = _recorder()
    with pytest.raises(ConfigError, match="requires --yes when stdin is not interactive"):
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

    outcome = _run(
        interactive=True,
        confirms=[True],
        items=["a", "b"],
        action=action,
        label=lambda item: f"id={item}",
    )

    assert "error: id=a: boom" in capsys.readouterr().err
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
    with pytest.raises(ConfigError, match="requires --yes"):
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


def test_finish_exits_1_on_partial_failure() -> None:
    outcome = BatchOutcome(results=[("a", "a")], failed=1, planned_rows=[{}, {}])
    with pytest.raises(SystemExit) as excinfo:
        finish(outcome)
    assert excinfo.value.code == 1


def test_finish_returns_on_success() -> None:
    finish(BatchOutcome(results=[("a", "a")], failed=0, planned_rows=[{}]))


def test_finish_accepts_any_failed_bool() -> None:
    with pytest.raises(SystemExit):
        finish(True)
    finish(False)
