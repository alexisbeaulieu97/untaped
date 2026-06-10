"""Testing helpers for Cyclopts command apps."""

from __future__ import annotations

import io
import sys
from collections.abc import Callable, Iterable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from typing import Any

from cyclopts import App
from rich.console import Console

from untaped.cli import run_cyclopts_app


@dataclass(frozen=True)
class CliResult:
    """Captured CLI invocation result."""

    exit_code: int
    stdout: str
    stderr: str
    exception: BaseException | None = None

    @property
    def output(self) -> str:
        """Combined stdout and stderr, matching Click's test result ergonomics."""
        return f"{self.stdout}{self.stderr}"


class CliInvoker:
    """Small test runner wrapper around :func:`invoke_cli`."""

    def invoke(
        self,
        command: App | Callable[..., Any],
        args: Iterable[str] | None = None,
        *,
        input: str | None = None,
        catch_exceptions: bool = True,
    ) -> CliResult:
        """Invoke a command with captured stdout/stderr."""
        return invoke_cli(
            command,
            args,
            input=input,
            catch_exceptions=catch_exceptions,
        )


def invoke_cli(
    command: App | Callable[..., Any],
    args: Iterable[str] | None = None,
    *,
    input: str | None = None,
    catch_exceptions: bool = True,
) -> CliResult:
    """Invoke a Cyclopts app or launcher while capturing terminal streams."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    previous_stdin = sys.stdin
    sys.stdin = io.StringIO(input or "")
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                _call_command(command, list(args or []), stdout=stdout, stderr=stderr)
            except SystemExit as exc:
                return CliResult(
                    exit_code=_exit_code(exc),
                    stdout=stdout.getvalue(),
                    stderr=stderr.getvalue(),
                    exception=exc,
                )
            except BaseException as exc:
                if not catch_exceptions:
                    raise
                return CliResult(
                    exit_code=1,
                    stdout=stdout.getvalue(),
                    stderr=stderr.getvalue(),
                    exception=exc,
                )
    finally:
        sys.stdin = previous_stdin
    return CliResult(exit_code=0, stdout=stdout.getvalue(), stderr=stderr.getvalue())


def _call_command(
    command: App | Callable[..., Any],
    args: list[str],
    *,
    stdout: io.StringIO,
    stderr: io.StringIO,
) -> None:
    console = _console(stdout)
    error_console = _console(stderr)
    if isinstance(command, App):
        target = command.meta if command.meta.default_command is not None else command
        run_cyclopts_app(
            target,
            args,
            console=console,
            error_console=error_console,
            result_action="print_non_int_sys_exit",
        )
        return
    command(args, console=console, error_console=error_console)


def _console(stream: io.StringIO) -> Console:
    return Console(
        file=stream,
        force_terminal=False,
        color_system=None,
        width=120,
    )


def _exit_code(exc: SystemExit) -> int:
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1
