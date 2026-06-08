"""Tests for shared workspace CLI helpers."""

from __future__ import annotations

import pytest
from untaped import ConfigError

from untaped_workspace.cli.common import confirm


def test_confirm_uses_core_ui_context(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class _PromptUi:
        def confirm(self, message: str, *, default: bool = False) -> bool:
            seen["message"] = message
            seen["default"] = default
            return True

    def _ui_context(*_: object, **__: object) -> _PromptUi:
        seen["context_called"] = True
        return _PromptUi()

    monkeypatch.setattr("untaped_workspace.cli.common._stdin_is_interactive", lambda: True)
    monkeypatch.setattr("untaped_workspace.cli.common.ui_context", _ui_context)

    assert confirm("prune workspace?", yes=False) is True
    assert seen == {
        "context_called": True,
        "message": "prune workspace?",
        "default": False,
    }


def test_confirm_yes_bypasses_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    def _ui_context(*_: object, **__: object) -> object:
        raise AssertionError("should not prompt with --yes")

    monkeypatch.setattr("untaped_workspace.cli.common.ui_context", _ui_context)

    assert confirm("prune workspace?", yes=True) is True


def test_confirm_non_interactive_requires_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("untaped_workspace.cli.common._stdin_is_interactive", lambda: False)

    with pytest.raises(ConfigError, match="requires --yes"):
        confirm("prune workspace?", yes=False)
