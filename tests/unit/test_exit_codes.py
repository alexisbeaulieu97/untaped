"""The exit-code contract: error classes, interrupts, shared options, prompts.

``docs/conventions.md`` fixes one meaning per code (0 ok, 1 failure or
decline, 2 usage, 3 predicate hit, 130 interrupted); these tests pin how the
core helpers select them.
"""

from __future__ import annotations

import io

import pytest
from cyclopts import App

from untaped.cli import LimitOption, ParallelOption, report_errors
from untaped.errors import (
    ConfigError,
    ExitCode,
    OperationCancelledError,
    PromptInterruptedError,
    UntapedError,
    UsageError,
)
from untaped.testing import ScriptedPromptBackend, TtyStringIO, invoke_cli
from untaped.ui import UiContext


def test_exit_code_values_are_the_documented_contract() -> None:
    assert [int(code) for code in ExitCode] == [0, 1, 2, 3, 130]


@pytest.mark.parametrize(
    ("error", "code", "line"),
    [
        (UntapedError("boom"), 1, "error: boom"),
        (ConfigError("bad config"), 1, "error: bad config"),
        (UsageError("--a and --b cannot be combined"), 2, "error: --a and --b cannot be combined"),
        (OperationCancelledError(), 1, "cancelled; no changes made"),
        (PromptInterruptedError("prompt cancelled"), 130, "error: prompt cancelled"),
    ],
)
def test_report_errors_exits_with_the_error_class_code(
    error: UntapedError, code: int, line: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info, report_errors():
        raise error

    assert exc_info.value.code == code
    assert capsys.readouterr().err == f"{line}\n"


def _app_raising(exc: BaseException) -> App:
    app = App(name="demo")

    @app.default
    def run() -> None:
        raise exc

    return app


def test_ctrl_c_anywhere_exits_130_without_a_traceback() -> None:
    result = invoke_cli(_app_raising(KeyboardInterrupt()), [])

    assert result.exit_code == 130
    assert "Traceback" not in result.output


def test_ctrl_c_at_a_prompt_exits_130() -> None:
    class _Interrupting(ScriptedPromptBackend):
        def confirm(self, message: str, *, default: bool) -> bool:
            raise KeyboardInterrupt

    app = App(name="demo")

    @app.default
    def run() -> None:
        with report_errors():
            UiContext(stdin=TtyStringIO(), prompt_backend=_Interrupting()).confirm("Go?")

    result = invoke_cli(app, [])

    assert result.exit_code == 130
    assert "error: prompt cancelled" in result.stderr


@pytest.mark.parametrize("args", [["--parallel", "0"], ["-j", "-1"], ["--limit", "0"]])
def test_shared_numeric_options_reject_values_below_one_as_usage(args: list[str]) -> None:
    app = App(name="demo")

    @app.default
    def run(*, parallel: ParallelOption = 1, limit: LimitOption = None) -> None:
        del parallel, limit

    result = invoke_cli(app, args)

    assert result.exit_code == 2
    assert result.stderr.startswith("error:")


def test_success_lines_honour_quiet() -> None:
    loud, quiet = io.StringIO(), io.StringIO()
    UiContext(stderr=loud).success("created profile: prod")
    UiContext(stderr=quiet, quiet=True).success("created profile: prod")

    assert "created profile: prod" in loud.getvalue()
    assert quiet.getvalue() == ""


def test_confirm_action_needs_a_terminal_or_yes() -> None:
    ui = UiContext(stdin=io.StringIO(), prompt_backend=ScriptedPromptBackend(confirms=[True]))

    assert ui.confirm_action("Delete?", assume_yes=True) is True
    with pytest.raises(UsageError, match="prune requires --yes when not interactive"):
        ui.confirm_action("Delete?", refusal="prune requires --yes when not interactive")
