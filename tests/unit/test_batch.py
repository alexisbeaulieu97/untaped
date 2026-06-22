"""Behavioural tests for the shared ``batch_apply`` helper."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

import pytest

from untaped.batch import batch_apply
from untaped.errors import ConfigError, HttpError


class _Handle:
    def update(
        self, message: str, *, fraction: float | None = None, new_phase: bool = False
    ) -> None:
        pass


class _FakeUi:
    """Stub UiContext: records confirm calls, no-op progress."""

    def __init__(self, *, answer: bool = True) -> None:
        self.answer = answer
        self.confirms: list[dict[str, Any]] = []

    def confirm(self, message: str, *, default: bool = False) -> bool:
        self.confirms.append({"message": message, "default": default})
        return self.answer

    @contextmanager
    def progress(self, label: str):  # type: ignore[no-untyped-def]
        yield _Handle()


def _describe(item: str) -> dict[str, object]:
    return {"id": item, "name": f"name-{item}"}


def _recorder() -> tuple[Callable[[str], str], list[str]]:
    """An action that records the items it was called with."""
    calls: list[str] = []

    def action(item: str) -> str:
        calls.append(item)
        return f"done-{item}"

    return action, calls


def _run(monkeypatch: pytest.MonkeyPatch, *, interactive: bool, **kwargs: Any) -> Any:
    monkeypatch.setattr("untaped.batch._stdin_is_interactive", lambda: interactive)
    kwargs.setdefault("verb", "delete")
    kwargs.setdefault("noun", "Widget")
    kwargs.setdefault("label", lambda item: item)
    kwargs.setdefault("describe", _describe)
    kwargs.setdefault("destructive", True)
    return batch_apply(**kwargs)


def test_interactive_destructive_previews_and_confirms(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    action, calls = _recorder()
    ui = _FakeUi()
    outcome = _run(monkeypatch, interactive=True, items=["a", "b"], action=action, ui=ui)

    captured = capsys.readouterr()
    assert "About to delete 2 Widget(s):" in captured.err
    assert "name-a" in captured.err and "name-b" in captured.err
    assert captured.out == ""  # preview/progress stay off stdout
    assert ui.confirms == [{"message": "Continue?", "default": False}]
    assert calls == ["a", "b"]
    assert outcome.failed == 0
    assert outcome.results == [("a", "done-a"), ("b", "done-b")]


def test_interactive_destructive_can_skip_generic_preview(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    action, calls = _recorder()
    ui = _FakeUi()
    outcome = _run(
        monkeypatch,
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
    assert ui.confirms == [{"message": "Continue?", "default": False}]
    assert calls == ["a", "b"]
    assert outcome.failed == 0
    assert outcome.results == [("a", "done-a"), ("b", "done-b")]


def test_decline_runs_no_action(monkeypatch: pytest.MonkeyPatch) -> None:
    action, calls = _recorder()
    outcome = _run(
        monkeypatch, interactive=True, items=["a", "b"], action=action, ui=_FakeUi(answer=False)
    )

    assert calls == []
    assert outcome.results == []
    assert outcome.total == 2
    assert len(outcome.planned_rows) == 2


def test_assume_yes_skips_gate_and_preview(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    action, calls = _recorder()
    ui = _FakeUi()
    outcome = _run(
        monkeypatch, interactive=True, items=["a", "b"], action=action, ui=ui, assume_yes=True
    )

    assert "About to delete" not in capsys.readouterr().err
    assert ui.confirms == []
    assert calls == ["a", "b"]
    assert outcome.failed == 0


def test_assume_yes_skips_generic_preview_even_when_enabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    action, calls = _recorder()
    ui = _FakeUi()
    outcome = _run(
        monkeypatch,
        interactive=True,
        items=["a", "b"],
        action=action,
        ui=ui,
        assume_yes=True,
        render_generic_preview=True,
    )

    assert "About to delete" not in capsys.readouterr().err
    assert ui.confirms == []
    assert calls == ["a", "b"]
    assert outcome.failed == 0


def test_benign_verb_skips_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    action, calls = _recorder()
    # Non-interactive + no --yes would refuse a destructive verb; benign runs.
    outcome = _run(
        monkeypatch,
        interactive=False,
        items=["a", "b"],
        action=action,
        ui=_FakeUi(),
        destructive=False,
    )

    assert calls == ["a", "b"]
    assert outcome.results == [("a", "done-a"), ("b", "done-b")]


def test_destructive_non_interactive_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    action, calls = _recorder()
    with pytest.raises(ConfigError, match="requires --yes when stdin is not interactive"):
        _run(monkeypatch, interactive=False, items=["a", "b"], action=action, ui=_FakeUi())
    assert calls == []


def test_destructive_non_interactive_refuses_before_generic_preview(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    action, calls = _recorder()
    with pytest.raises(ConfigError, match="requires --yes when stdin is not interactive"):
        _run(
            monkeypatch,
            interactive=False,
            items=["a", "b"],
            action=action,
            ui=_FakeUi(),
            render_generic_preview=False,
        )
    assert calls == []
    assert "About to delete" not in capsys.readouterr().err


def test_preview_only_returns_plan_without_acting(monkeypatch: pytest.MonkeyPatch) -> None:
    action, calls = _recorder()
    ui = _FakeUi()
    outcome = _run(
        monkeypatch, interactive=True, items=["a", "b"], action=action, ui=ui, preview_only=True
    )

    assert calls == []
    assert ui.confirms == []
    assert outcome.planned_rows == [_describe("a"), _describe("b")]


def test_partial_failure_counts_and_continues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def action(item: str) -> str:
        if item == "a":
            raise HttpError("boom")
        return f"done-{item}"

    outcome = _run(
        monkeypatch,
        interactive=True,
        items=["a", "b"],
        action=action,
        ui=_FakeUi(),
        label=lambda item: f"id={item}",
    )

    assert "error: id=a: boom" in capsys.readouterr().err
    assert outcome.failed == 1
    assert outcome.results == [("b", "done-b")]
    assert outcome.any_failed


def test_empty_items_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    action, calls = _recorder()
    ui = _FakeUi()
    outcome = _run(monkeypatch, interactive=True, items=[], action=action, ui=ui)

    assert calls == []
    assert ui.confirms == []
    assert outcome.total == 0
    assert outcome.results == []
    assert outcome.planned_rows == []


def test_non_untaped_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def action(item: str) -> str:
        raise ValueError("bug")

    with pytest.raises(ValueError, match="bug"):
        _run(
            monkeypatch,
            interactive=False,
            items=["a"],
            action=action,
            ui=_FakeUi(),
            assume_yes=True,
        )
