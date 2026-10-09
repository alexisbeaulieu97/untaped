"""Testing helpers for driving Cyclopts command apps with captured output.

:func:`check_conventions` checks one installed plugin against
``docs/reference/conventions.md``; its ``candidates`` argument composes a provider
passed in directly instead of one discovered through entry points.
:func:`invoke_root` runs ``untaped ...`` in-process against the installed
providers.
"""

from __future__ import annotations

import io
import sys
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO, cast

from cyclopts import App
from rich.console import Console

from untaped.cli import run_cyclopts_app
from untaped.errors import ConfigError, PromptInterruptedError
from untaped.prompts import (
    PromptBackend,
    PromptChoice,
    reset_prompt_backend_override,
    reset_terminal_override,
    set_prompt_backend_override,
    set_terminal_override,
)
from untaped.screen.core import Cancel, Quit, Screen
from untaped.stability import apply_marks
from untaped.testing.screens import ScreenKeys, ScreenRun, drive_screen

if TYPE_CHECKING:
    from untaped.picker import PickRequest, PickResult
    from untaped.plugins.registry import PluginSpec, ProviderCandidate
    from untaped.theme import ThemeSpec

__all__ = [
    "CliInvoker",
    "CliResult",
    "PromptBackend",
    "ScreenKeys",
    "ScreenRun",
    "ScriptedPromptBackend",
    "TtyStringIO",
    "assert_destructive_contract",
    "check_conventions",
    "drive_screen",
    "invoke_cli",
    "invoke_root",
    "provider_candidate",
]


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
        terminal: bool = False,
    ) -> CliResult:
        """Invoke a command with captured stdout/stderr."""
        return invoke_cli(
            command,
            args,
            input=input,
            catch_exceptions=catch_exceptions,
            interactive=interactive,
            prompt_backend=prompt_backend,
            terminal=terminal,
        )


def invoke_cli(
    command: App | Callable[..., Any],
    args: Iterable[str] | None = None,
    *,
    input: str | None = None,
    catch_exceptions: bool = True,
    interactive: bool = False,
    prompt_backend: PromptBackend | None = None,
    terminal: bool = False,
) -> CliResult:
    """Invoke a Cyclopts app or launcher while capturing terminal streams.

    A bare app is given the panels and help lines its marks ask for first, as
    the root would when mounting it (a nested sub-app computes the path of its
    replacement from its own name only). A mark on a ``PluginSpec`` is
    applied only by a composed root, so assert a spec-marked plugin's help
    through :func:`invoke_root`, not through its own app.

    ``interactive=True`` swaps stdin for a :class:`TtyStringIO` so TTY gates
    open; ``prompt_backend`` installs a scripted backend for the invocation
    (reaching even ``UiContext``s the command builds itself). The
    controlling terminal (``/dev/tty``, used for prompts while stdin is
    piped) is simulated: absent by default, present with ``terminal=True``
    (prompts then go to ``prompt_backend``). A command that runs a screen
    (``ui.run``) needs neither with a :class:`ScriptedPromptBackend`, which
    never touches a terminal: ``prompt_backend=ScriptedPromptBackend(screens=[...])``
    is enough (and ``interactive=True`` only if the command checks for a TTY itself).
    """
    stdout = io.StringIO()
    stderr = io.StringIO()
    previous_stdin = sys.stdin
    sys.stdin = TtyStringIO(input or "") if interactive else io.StringIO(input or "")
    token = set_prompt_backend_override(prompt_backend) if prompt_backend is not None else None
    terminal_token = set_terminal_override(TtyStringIO if terminal else no_terminal)
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
        reset_terminal_override(terminal_token)
        if token is not None:
            reset_prompt_backend_override(token)
    return CliResult(exit_code=0, stdout=stdout.getvalue(), stderr=stderr.getvalue())


def invoke_root(argv: Sequence[str]) -> CliResult:
    """Run ``untaped *argv`` in-process, composed from the installed providers.

    The supported way for a plugin's tests to drive the real root: the root
    is composed through entry-point discovery, as the ``untaped`` script does,
    and ``argv`` is invoked on it with captured output.
    """
    from untaped.bootstrap import build_root_app  # noqa: PLC0415

    return CliInvoker().invoke(build_root_app().meta, list(argv))


def no_terminal() -> TextIO:
    """Terminal opener for tests without a controlling terminal."""
    raise OSError("no controlling terminal (test harness)")


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


def provider_candidate(
    spec: PluginSpec, *, distribution: str = "test-provider"
) -> ProviderCandidate:
    """``spec`` as a discovered candidate, for composing it without installing it.

    Pass the result to ``check_conventions(..., candidates=[...])`` or
    ``untaped.bootstrap.build_root_app(candidates=[...])``.
    """
    from untaped.plugins.registry import ProviderCandidate  # noqa: PLC0415

    return ProviderCandidate(distribution=distribution, name=spec.name, target=lambda: spec)


def check_conventions(
    plugin: str,
    *,
    tests_dir: Path | None = None,
    candidates: Sequence[ProviderCandidate] | None = None,
) -> None:
    """Fail with every convention violation of ``plugin``.

    The rules are in ``docs/reference/conventions.md#enforcement``.

    Checks the installed plugin's command subtree and its own source
    files: command grammar, stderr wording, package structure and layering.
    ``tests_dir`` adds the private-test-import check over those tests.
    ``candidates`` replaces entry-point discovery, so a test can compose a
    provider that is not installed. ``# untaped: allow <rule>`` on the flagged
    node's first line allows that one violation. A quarantined plugin
    fails with the reason it was refused, such as a broken rename declaration.
    """
    from untaped.conventions import plugin_violations  # noqa: PLC0415

    found = plugin_violations(plugin, tests_dir=tests_dir, candidates=candidates)
    assert not found, "convention violations:\n" + "\n".join(f"  {line}" for line in found)


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
    :class:`ConfigError` so a test fails cleanly instead of hanging. With
    ``interrupt=True`` every prompt raises :class:`PromptInterruptedError`,
    simulating Ctrl-C.

    ``screens`` answers :meth:`UiContext.run`: each entry is the screen's
    result (returned as ``Quit(entry)``), a :class:`Quit` (its result is
    returned, like the screen's own), a :class:`Cancel` (or the class itself), an exception
    instance or class (raised), or :class:`ScreenKeys` (replayed through
    :func:`drive_screen` with commands run synchronously, so a command-level
    test can run a real screen). ``ran`` keeps the screens it was asked to
    run. The backend never touches a terminal, so ``UiContext.run`` and
    ``UiContext.pick_many`` do not require one: a command test needs no
    ``terminal=True`` and no TTY stdin.
    """

    needs_terminal = False

    def __init__(
        self,
        *,
        confirms: Sequence[bool] = (),
        texts: Sequence[str] = (),
        secrets: Sequence[str] = (),
        selections: Sequence[Any] = (),
        multiselects: Sequence[list[Any]] = (),
        picks: Sequence[PickResult | None] = (),
        screens: Sequence[object] = (),
        interrupt: bool = False,
    ) -> None:
        self._interrupt = interrupt
        self._confirms = deque(confirms)
        self._texts = deque(texts)
        self._secrets = deque(secrets)
        self._selections = deque(selections)
        self._multiselects = deque(multiselects)
        self._picks = deque(picks)
        self._screens = deque(screens)
        self.ran: list[Screen[Any, Any]] = []
        self.calls: list[tuple[str, str]] = []

    def _next(self, queue: deque[Any], method: str, message: str) -> Any:
        self.calls.append((method, message))
        if self._interrupt:
            raise PromptInterruptedError("prompt cancelled")
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

    def pick_many(self, request: PickRequest) -> PickResult | None:
        return cast("PickResult | None", self._next(self._picks, "pick_many", request.heading))

    def run_screen[M, R](self, screen: Screen[M, R], *, theme: ThemeSpec) -> Quit[R] | Cancel:
        self.ran.append(screen)
        entry = self._next(self._screens, "run_screen", screen.title)
        if isinstance(entry, BaseException) or (
            isinstance(entry, type) and issubclass(entry, BaseException)
        ):
            raise entry
        if entry is Cancel:
            return Cancel()
        if isinstance(entry, Quit | Cancel):
            return cast("Quit[R] | Cancel", entry)
        if isinstance(entry, ScreenKeys):
            run = drive_screen(screen, entry, theme=theme, commands="sync")
            if run.outcome is None:
                raise ConfigError(
                    f"the scripted keys did not end screen {screen.title!r}; "
                    "finish them with a key that quits"
                )
            return run.outcome
        return cast("Quit[R]", Quit(entry))


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
        if target is command and command._meta_parent is None:
            # A composed root (or its meta app) marked its commands when it
            # mounted them; a bare plugin app did not. Never resolves a
            # lazy plugin.
            apply_marks(command, path=() if command.name == ("untaped",) else command.name[:1])
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
