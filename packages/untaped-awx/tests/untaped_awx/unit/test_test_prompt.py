"""UiPrompt: when suites may prompt, and which prompt each variable gets."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast

import pytest

from untaped.sdk import PromptChoice, Terminal
from untaped_awx.domain.suites.suite import VariableSpec
from untaped_awx.infrastructure.suites.prompt import UiPrompt


@pytest.mark.parametrize(
    ("can_prompt", "forced", "expected"),
    [(True, False, True), (False, False, False), (True, True, False)],
)
def test_is_interactive_follows_the_terminal_unless_forced(
    can_prompt: bool, forced: bool, expected: bool
) -> None:
    terminal = cast("Terminal", SimpleNamespace(can_prompt=can_prompt))
    assert UiPrompt(lambda: terminal, force_non_interactive=forced).is_interactive() is expected


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
def test_ask_routes_each_variable_to_its_prompt(spec: VariableSpec, answer: str) -> None:
    terminal = cast("Terminal", _PromptUi())
    assert UiPrompt(lambda: terminal).ask(spec) == answer
