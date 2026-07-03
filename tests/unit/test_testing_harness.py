"""Tests for the scripted-prompt test harness."""

import pytest

from untaped.errors import ConfigError
from untaped.testing import CliResult, ScriptedPromptBackend, TtyStringIO, invoke_cli
from untaped.ui import UiContext


def test_tty_stringio_reports_a_tty() -> None:
    assert TtyStringIO("y\n").isatty() is True


def test_scripted_backend_answers_in_order_and_records_calls() -> None:
    backend = ScriptedPromptBackend(confirms=[True, False], texts=["hello"])
    assert backend.confirm("Continue?", default=False) is True
    assert backend.confirm("Really?", default=False) is False
    assert backend.text("Name", default=None) == "hello"
    assert backend.calls == [
        ("confirm", "Continue?"),
        ("confirm", "Really?"),
        ("text", "Name"),
    ]


def test_scripted_backend_exhausted_queue_raises_config_error() -> None:
    backend = ScriptedPromptBackend()
    with pytest.raises(ConfigError, match="no scripted confirm answer"):
        backend.confirm("Continue?", default=False)


def test_ui_context_uses_override_backend_on_a_tty_stdin() -> None:
    backend = ScriptedPromptBackend(confirms=[False])
    ui = UiContext(stdin=TtyStringIO(), prompt_backend=backend)
    assert ui.confirm("Continue?") is False
    assert backend.calls == [("confirm", "Continue?")]


def test_invoke_cli_injects_backend_and_fake_tty() -> None:
    from untaped.ui import ui_context

    def command(args, *, console=None, error_console=None):
        ui = ui_context(strict=False)
        answer = ui.confirm("Proceed?")
        raise SystemExit(0 if answer else 1)

    backend = ScriptedPromptBackend(confirms=[False])
    result: CliResult = invoke_cli(command, [], interactive=True, prompt_backend=backend)
    assert result.exit_code == 1
    assert backend.calls == [("confirm", "Proceed?")]


def test_invoke_cli_default_stdin_is_not_a_tty() -> None:
    import sys

    def command(args, *, console=None, error_console=None):
        raise SystemExit(0 if sys.stdin.isatty() else 3)

    assert invoke_cli(command, []).exit_code == 3


def test_destructive_contract_passes_for_a_conforming_command() -> None:
    """A command wired through batch_apply(destructive=True) satisfies both legs."""
    from untaped.batch import batch_apply
    from untaped.testing import assert_destructive_contract
    from untaped.ui import ui_context

    executed: list[str] = []

    def command(args, *, console=None, error_console=None):
        ui = ui_context(strict=False)
        outcome = batch_apply(
            ["a"],
            lambda item: executed.append(item),
            verb="delete",
            noun="thing",
            label=str,
            describe=lambda item: {"name": item},
            ui=ui,
            destructive=True,
        )
        raise SystemExit(1 if outcome.any_failed else 0)

    assert_destructive_contract(command, [], assert_unchanged=lambda: None)
    assert executed == []


def test_destructive_contract_flags_a_missing_refusal() -> None:
    from untaped.testing import assert_destructive_contract

    def command(args, *, console=None, error_console=None):
        raise SystemExit(0)

    with pytest.raises(AssertionError, match="refuse"):
        assert_destructive_contract(command, [])
