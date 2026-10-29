"""Tests for the scripted-prompt test harness."""

import pytest

from untaped.sdk._ui import Ui
from untaped.sdk.errors import ConfigError
from untaped.sdk.testing import CliResult, ScriptedTerminal, TtyStringIO, invoke_cli


def test_tty_stringio_reports_a_tty() -> None:
    assert TtyStringIO("y\n").isatty() is True


def test_scripted_backend_answers_in_order_and_records_what_it_asked() -> None:
    backend = ScriptedTerminal().answers(True, False).types("hello")
    assert backend.confirm("Continue?", default=False) is True
    assert backend.confirm("Really?", default=False) is False
    assert backend.text("Name", default=None) == "hello"
    assert backend.asked == [
        ("confirm", "Continue?"),
        ("confirm", "Really?"),
        ("text", "Name"),
    ]


def test_scripted_backend_exhausted_queue_raises_config_error() -> None:
    backend = ScriptedTerminal().present()
    with pytest.raises(ConfigError, match="no scripted confirm answer"):
        backend.confirm("Continue?", default=False)


def test_the_terminal_uses_override_backend_on_a_tty_stdin() -> None:
    backend = ScriptedTerminal().answers(False)
    ui = Ui(stdin=TtyStringIO(), prompt_backend=backend)
    assert ui.confirm("Continue?") is False
    assert backend.asked == [("confirm", "Continue?")]


def test_invoke_cli_injects_backend_and_fake_tty() -> None:
    from untaped.sdk._ui import build_terminal

    def command(args, *, console=None, error_console=None):
        ui = build_terminal(strict=False)
        answer = ui.confirm("Proceed?")
        raise SystemExit(0 if answer else 1)

    backend = ScriptedTerminal().answers(False)
    result: CliResult = invoke_cli(command, [], interactive=True, prompt_backend=backend)
    assert result.exit_code == 1
    assert backend.asked == [("confirm", "Proceed?")]


def test_invoke_cli_default_stdin_is_not_a_tty() -> None:
    import sys

    def command(args, *, console=None, error_console=None):
        raise SystemExit(0 if sys.stdin.isatty() else 3)

    assert invoke_cli(command, []).exit_code == 3


def test_scripted_backend_interrupt_raises_prompt_interrupted() -> None:
    from untaped.sdk.errors import PromptInterruptedError

    backend = ScriptedTerminal().interrupts()
    with pytest.raises(PromptInterruptedError):
        backend.text("Name", default=None)
    assert backend.asked == [("text", "Name")]
