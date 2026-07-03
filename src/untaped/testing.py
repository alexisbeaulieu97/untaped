"""Testing helpers for driving Cyclopts command apps with captured output."""

from __future__ import annotations

import io
import sys
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from typing import Any

from cyclopts import App
from rich.console import Console

from untaped.cli import run_cyclopts_app
from untaped.errors import ConfigError
from untaped.prompts import (
    PromptBackend,
    PromptChoice,
    reset_prompt_backend_override,
    set_prompt_backend_override,
)


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
        interactive: bool = False,
        prompt_backend: PromptBackend | None = None,
    ) -> CliResult:
        """Invoke a command with captured stdout/stderr."""
        return invoke_cli(
            command,
            args,
            input=input,
            catch_exceptions=catch_exceptions,
            interactive=interactive,
            prompt_backend=prompt_backend,
        )


def invoke_cli(
    command: App | Callable[..., Any],
    args: Iterable[str] | None = None,
    *,
    input: str | None = None,
    catch_exceptions: bool = True,
    interactive: bool = False,
    prompt_backend: PromptBackend | None = None,
) -> CliResult:
    """Invoke a Cyclopts app or launcher while capturing terminal streams.

    ``interactive=True`` swaps stdin for a :class:`TtyStringIO` so TTY gates
    open; ``prompt_backend`` installs a scripted backend for the invocation
    (reaching even ``UiContext``s the command builds itself).
    """
    stdout = io.StringIO()
    stderr = io.StringIO()
    previous_stdin = sys.stdin
    sys.stdin = TtyStringIO(input or "") if interactive else io.StringIO(input or "")
    token = set_prompt_backend_override(prompt_backend) if prompt_backend is not None else None
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
        if token is not None:
            reset_prompt_backend_override(token)
    return CliResult(exit_code=0, stdout=stdout.getvalue(), stderr=stderr.getvalue())


def assert_destructive_contract(
    command: App | Callable[..., Any],
    args: Sequence[str],
    *,
    assert_unchanged: Callable[[], None] | None = None,
) -> None:
    """Assert a destructive verb honours the SDK confirmation contract.

    Invokes ``command`` twice with the same ``args`` (which must target the
    verb *positionally* — not via ``--stdin``, whose data leg would starve on
    the interactive run):

    1. **Piped stdin, no ``--yes``** → must refuse with the standard
       ``requires --yes`` error and a nonzero exit.
    2. **Interactive stdin, scripted decline** → must prompt exactly one
       confirm and honour the decline (no crash beyond ``SystemExit``).

    ``assert_unchanged`` (recommended) runs after each leg to verify the
    destructive effect did not happen — the helper cannot know what "no
    effect" means for the tool.
    """
    piped = invoke_cli(command, list(args))
    assert piped.exit_code != 0, (
        f"piped stdin without --yes must refuse, got exit {piped.exit_code}; "
        f"stderr: {piped.stderr!r}"
    )
    # The refusal surfaces either as a rendered `error: ... requires --yes`
    # line (command wrapped in report_errors) or as a caught ConfigError on
    # the result (bare command body) — accept both shapes.
    combined = f"{piped.stderr}{piped.stdout}{piped.exception or ''}"
    assert "requires --yes" in combined, (
        f"expected the standard 'requires --yes' refusal, got: {combined!r}"
    )
    if assert_unchanged is not None:
        assert_unchanged()

    backend = ScriptedPromptBackend(confirms=[False])
    interactive = invoke_cli(command, list(args), interactive=True, prompt_backend=backend)
    assert backend.calls and backend.calls[0][0] == "confirm", (
        "interactive path must prompt for confirmation before acting"
    )
    assert interactive.exception is None or isinstance(interactive.exception, SystemExit), (
        f"decline must exit cleanly, got {interactive.exception!r}"
    )
    if assert_unchanged is not None:
        assert_unchanged()


class TtyStringIO(io.StringIO):
    """A ``StringIO`` that claims to be a terminal.

    Used as the harness stdin for interactive-path tests: TTY checks
    (``UiContext``, ``batch_apply``) pass, while prompts are answered by a
    :class:`ScriptedPromptBackend` rather than a real terminal read.
    """

    def isatty(self) -> bool:
        return True


class ScriptedPromptBackend:
    """A :class:`~untaped.prompts.PromptBackend` answering from fixed queues.

    Each prompt method pops its next scripted answer and records
    ``(method, message)`` in ``calls``; an exhausted queue raises
    :class:`ConfigError` so a test fails cleanly instead of hanging.
    """

    def __init__(
        self,
        *,
        confirms: Sequence[bool] = (),
        texts: Sequence[str] = (),
        secrets: Sequence[str] = (),
        selections: Sequence[Any] = (),
        multiselects: Sequence[list[Any]] = (),
    ) -> None:
        self._confirms = deque(confirms)
        self._texts = deque(texts)
        self._secrets = deque(secrets)
        self._selections = deque(selections)
        self._multiselects = deque(multiselects)
        self.calls: list[tuple[str, str]] = []

    def _next(self, queue: deque[Any], method: str, message: str) -> Any:
        self.calls.append((method, message))
        if not queue:
            raise ConfigError(f"no scripted {method} answer for prompt {message!r}")
        return queue.popleft()

    def confirm(self, message: str, *, default: bool) -> bool:
        return bool(self._next(self._confirms, "confirm", message))

    def text(self, message: str, *, default: str | None) -> str:
        return str(self._next(self._texts, "text", message))

    def secret(self, message: str, *, confirmation: bool) -> str:
        return str(self._next(self._secrets, "secret", message))

    def select(
        self,
        message: str,
        choices: Sequence[PromptChoice[Any]],
        *,
        default: Any | None,
        search: bool,
    ) -> Any:
        return self._next(self._selections, "select", message)

    def multiselect(
        self,
        message: str,
        choices: Sequence[PromptChoice[Any]],
        *,
        defaults: Sequence[Any],
    ) -> list[Any]:
        return list(self._next(self._multiselects, "multiselect", message))


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
