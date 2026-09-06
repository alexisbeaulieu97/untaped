"""Quick smoke for UiPrompt: interactivity check + core prompt routing."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from untaped.api import ConfigError, PromptChoice
from untaped.capabilities.awx.domain.test_suite import VariableSpec
from untaped.capabilities.awx.infrastructure.test.prompt import UiPrompt


def test_is_interactive_when_stdin_is_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    assert UiPrompt().is_interactive() is True


def test_not_interactive_when_stdin_redirected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    assert UiPrompt().is_interactive() is False


def test_force_non_interactive_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert UiPrompt(force_non_interactive=True).is_interactive() is False


def test_visible_prompt_uses_core_text(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class _PromptUi:
        def text(
            self,
            message: str,
            *,
            default: str | None = None,
            required: bool = True,
        ) -> str:
            seen["message"] = message
            seen["default"] = default
            seen["required"] = required
            return "answer"

    monkeypatch.setattr(
        "untaped.capabilities.awx.infrastructure.test.prompt.ui_context",
        lambda **_: _PromptUi(),
    )

    assert UiPrompt().ask(VariableSpec(name="env", description="Environment")) == "answer"
    assert seen == {"message": "Environment", "default": None, "required": True}


def test_secret_prompt_uses_core_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class _PromptUi:
        def secret(self, message: str, *, confirmation: bool = False, required: bool = True) -> str:
            seen["message"] = message
            seen["confirmation"] = confirmation
            seen["required"] = required
            return "s3cr3t"

    monkeypatch.setattr(
        "untaped.capabilities.awx.infrastructure.test.prompt.ui_context",
        lambda **_: _PromptUi(),
    )

    assert UiPrompt().ask(VariableSpec(name="token", secret=True)) == "s3cr3t"
    assert seen == {"message": "token", "confirmation": False, "required": True}


def test_choice_prompt_uses_core_select(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class _PromptUi:
        def select(
            self,
            message: str,
            choices: Sequence[PromptChoice[str]],
            *,
            default: str | None = None,
            search: bool = False,
        ) -> str:
            seen["message"] = message
            seen["choices"] = [(choice.value, choice.label) for choice in choices]
            seen["default"] = default
            seen["search"] = search
            return "prod"

    monkeypatch.setattr(
        "untaped.capabilities.awx.infrastructure.test.prompt.ui_context",
        lambda **_: _PromptUi(),
    )

    answer = UiPrompt().ask(VariableSpec(name="env", type="choice", choices=("dev", "prod")))

    assert answer == "prod"
    assert seen == {
        "message": "env",
        "choices": [("dev", "dev"), ("prod", "prod")],
        "default": None,
        "search": False,
    }


def test_prompt_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    class _PromptUi:
        def text(
            self,
            message: str,
            *,
            default: str | None = None,
            required: bool = True,
        ) -> str:
            raise ConfigError("prompt cancelled")

    monkeypatch.setattr(
        "untaped.capabilities.awx.infrastructure.test.prompt.ui_context",
        lambda **_: _PromptUi(),
    )

    with pytest.raises(ConfigError, match="prompt cancelled"):
        UiPrompt().ask(VariableSpec(name="env", type="string"))
