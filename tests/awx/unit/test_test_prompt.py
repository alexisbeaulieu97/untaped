"""UiPrompt: when suites may prompt, and which prompt each variable gets."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from untaped.capabilities.awx.domain.suite import VariableSpec
from untaped.capabilities.awx.infrastructure.suites.prompt import UiPrompt
from untaped.capability_api import PromptChoice


@pytest.mark.parametrize(
    ("stdin_tty", "stderr_tty", "forced", "expected"),
    [
        # stderr redirection (``2>/dev/null``) must not disable prompts
        (True, False, False, True),
        (False, True, False, False),
        (True, True, True, False),
    ],
)
def test_is_interactive_follows_stdin_only(
    monkeypatch: pytest.MonkeyPatch, stdin_tty: bool, stderr_tty: bool, forced: bool, expected: bool
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: stdin_tty)
    monkeypatch.setattr("sys.stderr.isatty", lambda: stderr_tty)
    assert UiPrompt(force_non_interactive=forced).is_interactive() is expected


class _PromptUi:
    def text(self, message: str) -> str:
        return f"text:{message}"

    def secret(self, message: str) -> str:
        return f"secret:{message}"

    def select(self, message: str, choices: Sequence[PromptChoice[str]]) -> str:
        return f"select:{message}:" + ",".join(choice.value for choice in choices)


@pytest.mark.parametrize(
    ("spec", "answer"),
    [
        (VariableSpec(name="env", description="Environment"), "text:Environment"),
        (VariableSpec(name="token", secret=True), "secret:token"),
        (VariableSpec(name="env", type="choice", choices=("dev", "prod")), "select:env:dev,prod"),
    ],
)
def test_ask_routes_each_variable_to_its_prompt(
    monkeypatch: pytest.MonkeyPatch, spec: VariableSpec, answer: str
) -> None:
    monkeypatch.setattr(
        "untaped.capabilities.awx.infrastructure.suites.prompt.ui_context",
        lambda **_: _PromptUi(),
    )
    assert UiPrompt().ask(spec) == answer
