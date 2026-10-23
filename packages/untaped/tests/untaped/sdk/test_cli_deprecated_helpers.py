"""The stderr helpers ``ctx.diag`` replaces warn until 12.0; untaped's own code never calls them."""

from __future__ import annotations

import io
import warnings
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout

import pytest

from untaped.sdk._cli import echo, report_error, report_row_errors
from untaped.sdk._ui import UiContext
from untaped.sdk.diagnostic import Failure
from untaped.sdk.errors import UntapedError


def _progress() -> None:
    with UiContext().progress("x"):
        pass


OLD: dict[str, Callable[[], object]] = {
    "UiContext.message": lambda: UiContext().message("info", "x"),
    "UiContext.success": lambda: UiContext().success("x"),
    "UiContext.progress": _progress,
    "report_error": lambda: report_error(UntapedError("x")),
    "report_row_errors": lambda: report_row_errors([], item=str),
    "echo(err=True)": lambda: echo("x", err=True),
    "echo(err=False)": lambda: echo("x", err=False),
    "UiContext.styled(err=True)": lambda: UiContext().styled("x", err=True),
}


@pytest.mark.parametrize("name", sorted(OLD))
def test_each_old_helper_warns_and_names_its_replacement(name: str) -> None:
    with (
        redirect_stderr(io.StringIO()),
        pytest.warns(DeprecationWarning, match=r"removed in 12\.0; use .*(ctx\.diag|Diagnostics)"),
    ):
        OLD[name]()


def test_stdout_echo_and_styled_do_not_warn() -> None:
    with redirect_stdout(io.StringIO()), warnings.catch_warnings():
        warnings.simplefilter("error")
        echo("x")
        UiContext(stdout=io.StringIO()).styled("x")


def test_untaped_code_calling_an_old_helper_fails_the_suite() -> None:
    """``filterwarnings`` turns the warning into an error in an ``untaped`` module."""
    probe = {"__name__": "untaped.probe", "UiContext": UiContext}
    code = compile("UiContext().message('info', 'x')", "<untaped.probe>", "exec")
    with redirect_stderr(io.StringIO()), pytest.raises(DeprecationWarning, match=r"ctx\.diag"):
        exec(code, probe)


def test_a_row_failure_still_reports_through_the_old_helper() -> None:
    row = type("Row", (), {"error": Failure.plain("gone"), "name": "b"})()
    stderr = io.StringIO()
    with redirect_stderr(stderr), pytest.warns(DeprecationWarning):
        report_row_errors([row], item=lambda each: each.name)
    assert stderr.getvalue() == "error: b: gone\n"
