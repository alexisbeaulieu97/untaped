"""Quick smoke for TyperPrompt: interactivity check + stderr routing."""

from __future__ import annotations

import pytest
from untaped_awx.domain.test_suite import VariableSpec
from untaped_awx.infrastructure.test.prompt import TyperPrompt


def test_is_interactive_when_stdin_is_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    assert TyperPrompt().is_interactive() is True


def test_not_interactive_when_stdin_redirected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    assert TyperPrompt().is_interactive() is False


def test_force_non_interactive_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert TyperPrompt(force_non_interactive=True).is_interactive() is False


def test_prompt_routes_to_stderr_keeping_stdout_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prompts must go to stderr so ``--format json > out.json`` pipes are unpolluted."""
    captured: dict[str, object] = {}

    def fake_typer_prompt(text: str, **kwargs: object) -> str:
        captured["text"] = text
        captured.update(kwargs)
        return "answer"

    monkeypatch.setattr("untaped_awx.infrastructure.test.prompt.typer.prompt", fake_typer_prompt)
    TyperPrompt().ask(VariableSpec(name="env", type="string"))

    assert captured["err"] is True
