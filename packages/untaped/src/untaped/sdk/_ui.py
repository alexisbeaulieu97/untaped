"""A command's terminal (``Terminal``): prompts, pickers and screens; and core's themed stdout."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, TextIO

from untaped.runtime.render import (
    Row,
    render_collection,
    render_detail,
    render_message,
    render_styled,
    should_colorize,
    stream_is_tty,
)
from untaped.runtime.sink import count_row_failures, current_sink
from untaped.runtime.theme import BUILTIN_THEMES, OutputFormat, resolve_theme_or_default
from untaped.sdk._run import is_quiet, is_verbose
from untaped.sdk._theme import ThemeSpec
from untaped.sdk.diagnostic import Message
from untaped.sdk.errors import (
    ConfigError,
    OperationCancelledError,
    PromptInterruptedError,
    UsageError,
)
from untaped.sdk.marks import experimental
from untaped.sdk.prompt.prompts import (
    PromptBackend,
    PromptChoice,
    TerminalPromptBackend,
    handle_prompt_exception,
    open_controlling_terminal,
    prompt_backend_override,
)

if TYPE_CHECKING:
    from rich.text import Text

    from untaped.screen.core import Screen
    from untaped.screen.picker import PickRequest, PickResult


class Terminal:
    """The terminal a command asks through: prompts, pickers and screens (``self.ctx.terminal``).

    The run makes the one instance; a command never builds its own. What it
    prints to stdout goes through its ``Output``.
    """

    def __init__(
        self,
        *,
        theme: ThemeSpec | None = None,
        prompt_backend: PromptBackend | None = None,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        self.theme = theme or BUILTIN_THEMES["default"]
        self.verbose = verbose
        self.quiet = quiet
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self._prompt_backend = prompt_backend
        self._default_prompt_backend: TerminalPromptBackend | None = None

    @property
    def can_prompt(self) -> bool:
        """Whether this context's stdin is a terminal, so prompts can read from it.

        Only stdin matters (a redirected stderr still prompts); a closed or
        broken stream is no terminal. Inside :meth:`terminal` it is ``True``.
        """
        return stream_is_tty(self.stdin)

    @property
    def prompt_backend(self) -> PromptBackend:
        """The interactive prompt backend, built lazily on first use.

        The default backend runs every prompt as a screen on the terminal
        adapter, which imports ``prompt_toolkit`` only when a prompt runs, so
        a rendering-only or piped invocation that never prompts pays nothing.
        Building it here, on first use, keeps it that way. An injected backend
        (tests, alternative frontends) is returned as-is. The default is cached
        only while its input and error streams remain the same, so reopened
        terminals never reuse closed streams. A ContextVar override (installed
        by the test harness via ``untaped.sdk.testing``) wins over the lazy default
        so scripted prompts reach contexts the test never constructed itself.
        """
        backend = self._prompt_backend
        if backend is not None:
            return backend

        override = prompt_backend_override()
        if override is not None:
            return override

        cached = self._default_prompt_backend
        if cached is not None and cached.stdin is self.stdin and cached.stderr is self.stderr:
            return cached

        backend = TerminalPromptBackend(
            stdin=self.stdin,
            stderr=self.stderr,
            theme=self.theme,
        )
        self._default_prompt_backend = backend
        return backend

    def confirm(self, message: str, *, default: bool = False) -> bool:
        """Prompt for a yes/no response."""
        with self._prompt_terminal():
            try:
                return self.prompt_backend.confirm(message, default=default)
            except (ConfigError, EOFError, KeyboardInterrupt) as exc:
                raise handle_prompt_exception(exc) from exc

    @contextmanager
    def terminal(
        self, *, refusal: str = "interactive input requires a terminal"
    ) -> Iterator[Terminal]:
        """Point prompts at a terminal for the duration of the block.

        Gives a prompt its input: a TTY stdin is used as-is; piped stdin is swapped
        for the controlling terminal (``/dev/tty``) and restored afterwards. The
        prompts themselves put their frames on a terminal too, even when stderr is
        redirected. A screen asks for its terminal through :meth:`run` or
        :meth:`pick_many`. Raises :class:`UsageError` with ``refusal`` when no
        terminal is available.
        """
        if self.can_prompt:
            yield self
            return
        try:
            terminal = open_controlling_terminal()
        except OSError as exc:
            raise UsageError(refusal) from exc
        original = self.stdin
        self.stdin = terminal
        try:
            yield self
        finally:
            self.stdin = original
            terminal.close()

    @contextmanager
    def _screen_terminal(self, *, command: str, alternative: str) -> Iterator[Terminal]:
        """Point a screen at a terminal for the duration of the block.

        What :meth:`run` and :meth:`pick_many` wrap a screen in. Raises
        :class:`UsageError` naming ``command`` and ``alternative`` when no terminal
        can be opened (see :meth:`_terminal_streams`).
        """
        with self._terminal_streams(refusal=no_terminal_message(command, alternative)):
            yield self

    @contextmanager
    def _prompt_terminal(self) -> Iterator[Terminal]:
        """Point a one-shot prompt at a terminal for the duration of the block.

        stdin must be a terminal already (:meth:`terminal` secures one for piped
        input) or :class:`UsageError` says so; the frames then go to stderr when it is
        a terminal and to the controlling terminal when it is redirected, so a prompt
        never paints into a log (see :meth:`_terminal_streams`).
        """
        self._ensure_promptable()
        with self._terminal_streams(refusal="interactive prompt requires a terminal"):
            yield self

    @contextmanager
    def _terminal_streams(self, *, refusal: str) -> Iterator[None]:
        """The one place a screen or prompt gets its terminal: stdin and stderr, both.

        They are used as they are when both are terminals. Otherwise (piped
        stdin, redirected stderr) the controlling terminal is opened for input and
        again for output, swapped in for the block and closed after, so nothing is
        ever painted into a file. A prompt backend that declares
        ``needs_terminal = False`` (an optional attribute; the scripted backend does)
        gets the streams as they are. Raises :class:`UsageError` with ``refusal``
        when no terminal can be opened.
        """
        injected = (
            self._prompt_backend if self._prompt_backend is not None else prompt_backend_override()
        )
        if not getattr(injected, "needs_terminal", True) or (
            self.can_prompt and stream_is_tty(self.stderr)
        ):
            yield
            return
        try:
            terminal_in = open_controlling_terminal()
        except OSError as exc:
            raise UsageError(refusal) from exc
        try:
            terminal_out = open_controlling_terminal(write=True)
        except OSError as exc:
            terminal_in.close()
            raise UsageError(refusal) from exc
        original = (self.stdin, self.stderr)
        self.stdin, self.stderr = terminal_in, terminal_out
        try:
            yield
        finally:
            self.stdin, self.stderr = original
            terminal_in.close()
            terminal_out.close()

    @experimental
    def require_screen_terminal(self, *, command: str, alternative: str) -> None:
        """Raise :class:`UsageError` naming ``command`` and ``alternative`` without a terminal.

        For a command that does work before it builds its screen (reading the config, probing
        a token store): ask first, so a run without a terminal touches nothing. :meth:`run`
        asks again.
        """
        with self._screen_terminal(command=command, alternative=alternative):
            pass

    @experimental
    def run[M, R](self, screen: Screen[M, R]) -> R:
        """Run an interactive screen and return its result.

        The screen draws on the terminal (stdin and stderr when both are
        terminals, otherwise the controlling terminal), with
        :class:`UsageError` naming its ``command`` and ``alternative`` when
        there is none. A prompt backend that declares ``needs_terminal = False``
        (the scripted backend of :mod:`untaped.sdk.testing`, which never touches a
        terminal) gets the screen without one. A user who quits without a
        result raises :class:`OperationCancelledError`; an interrupt raises
        :class:`PromptInterruptedError` (exit 130), like every prompt. stderr
        lines emitted while the screen runs print after it exits.
        """
        from untaped.screen.core import (  # noqa: PLC0415 - keeps screens off the startup path
            Cancel,
            Quit,
        )

        with self._screen_terminal(command=screen.command, alternative=screen.alternative):
            # Read inside the block: the default backend is built on the swapped streams.
            backend = self.prompt_backend
            run_screen: Callable[..., Quit[R] | Cancel] | None = getattr(
                backend, "run_screen", None
            )
            if run_screen is None:
                raise ConfigError(
                    f"prompt backend {type(backend).__name__} cannot run screens: "
                    "it has no run_screen method",
                    category="failed",
                    system="untaped",
                )
            try:
                with current_sink().hold():  # a line from a refresh never paints over it
                    outcome = run_screen(screen, theme=self.theme)
            except (ConfigError, EOFError, KeyboardInterrupt) as exc:
                raise handle_prompt_exception(exc) from exc
        if isinstance(outcome, Quit):
            return outcome.result
        if outcome.interrupted:
            raise PromptInterruptedError("prompt cancelled")
        raise OperationCancelledError

    def text(
        self,
        message: str,
        *,
        default: str | None = None,
        required: bool = True,
    ) -> str:
        """Prompt for visible text."""
        with self._prompt_terminal():
            try:
                value = self.prompt_backend.text(message, default=default)
            except (ConfigError, EOFError, KeyboardInterrupt) as exc:
                raise handle_prompt_exception(exc) from exc
        return self._validate_prompt_text(value, required=required)

    def secret(
        self,
        message: str,
        *,
        confirmation: bool = False,
        required: bool = True,
    ) -> str:
        """Prompt for hidden text."""
        with self._prompt_terminal():
            try:
                value = self.prompt_backend.secret(message, confirmation=confirmation)
            except (ConfigError, EOFError, KeyboardInterrupt) as exc:
                raise handle_prompt_exception(exc) from exc
        return self._validate_prompt_text(value, required=required)

    def select[T](
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        default: T | None = None,
        search: bool = False,
    ) -> T:
        """Prompt for one typed choice."""
        self._validate_choices(choices)
        with self._prompt_terminal():
            try:
                return self.prompt_backend.select(message, choices, default=default, search=search)
            except (ConfigError, EOFError, KeyboardInterrupt) as exc:
                raise handle_prompt_exception(exc) from exc

    def pick_many(self, request: PickRequest) -> PickResult:
        """Run the two-pane multi-select picker, a full-screen screen like every other.

        With piped stdin or a redirected stderr it draws on the controlling
        terminal (see :meth:`_screen_terminal`). Raises
        :class:`OperationCancelledError` when the user quits, and
        :class:`UsageError` naming ``request.command`` and ``request.alternative``
        (a generic message when unset) when there is no terminal at all.
        """
        ids = [item.id for item in request.catalog.items]
        if len(set(ids)) != len(ids):
            raise ConfigError(
                "picker items must have unique ids", category="failed", system="untaped"
            )
        with self._screen_terminal(command=request.command, alternative=request.alternative):
            try:
                with current_sink().hold():
                    picked = self.prompt_backend.pick_many(request)
            except (ConfigError, EOFError, KeyboardInterrupt) as exc:
                raise handle_prompt_exception(exc) from exc
        if picked is None:
            raise OperationCancelledError
        return picked

    def _ensure_promptable(self) -> None:
        if not self.can_prompt:
            raise UsageError("interactive prompt requires a TTY on stdin")

    @staticmethod
    def _validate_prompt_text(value: str, *, required: bool) -> str:
        if required and not value.strip():
            raise ConfigError("no value received from prompt", category="invalid")
        return value

    @staticmethod
    def _validate_choices[T](choices: Sequence[PromptChoice[T]]) -> None:
        if not choices:
            raise ConfigError(
                "prompt requires at least one choice", category="failed", system="untaped"
            )
        labels = [choice.label for choice in choices]
        if len(set(labels)) != len(labels):
            raise ConfigError(
                "prompt choices must have unique labels", category="failed", system="untaped"
            )


GENERIC_COMMAND = "this command"
"""What the no-terminal refusal names when a picker request carries no ``command``."""
GENERIC_ALTERNATIVE = "its non-interactive options (see --help)"
"""What the no-terminal refusal offers when a picker request carries no ``alternative``."""


def no_terminal_message(command: str, alternative: str) -> str:
    """The error for a screen with no terminal to draw on: the one place its text lives.

    An empty ``command`` or ``alternative`` (a picker request may leave them out)
    is said in words instead of quoted as a command.
    """
    named = f"`{command}`" if command else GENERIC_COMMAND
    offer = f"`{alternative}`" if alternative else GENERIC_ALTERNATIVE
    return f"{named} needs a terminal; use {offer}"


def build_terminal(
    *,
    theme: ThemeSpec | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    strict: bool = True,
) -> Terminal:
    """Build a terminal from the built-in theme presets.

    When ``theme`` is given it is used as-is (callers holding a resolved
    settings snapshot — e.g. ``AppContext.ui()`` — pass it so the context is
    not coupled to the live settings cache). Otherwise the active settings are
    read and the theme resolved from them; ``strict=False`` then degrades a
    settings :class:`ConfigError` to the default theme.
    """
    if theme is None:
        # Keep settings lazy so render-only imports avoid the settings chain.
        # Load only the ``ui`` section: an invalid value in an unrelated
        # section must not break rendering.
        from untaped.runtime.settings import load_core_part  # noqa: PLC0415

        theme = resolve_theme_or_default(lambda: load_core_part("ui"), strict=strict)
    return Terminal(
        theme=theme,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        verbose=is_verbose(),
        quiet=is_quiet(),
    )


# --- core's stdout in a terminal's theme: not plugin API, plugins print through Output ---


def collection(
    terminal: Terminal,
    rows: Sequence[Row],
    *,
    fmt: OutputFormat,
    columns: list[str] | None = None,
    empty: str | bool | None = None,
    kind: str | None = None,
) -> str:
    count_row_failures(rows)
    rendered = render_collection(
        rows,
        fmt=fmt,
        columns=columns,
        theme=terminal.theme,
        colorize=should_colorize(terminal.stdout),
        kind=kind,
    )
    if not rows and fmt == "table" and empty:
        _empty_note(terminal, empty if isinstance(empty, str) else "No results.")
    return rendered


def detail(
    terminal: Terminal,
    record: Row,
    *,
    fmt: OutputFormat,
    columns: list[str] | None = None,
    kind: str | None = None,
) -> str:
    count_row_failures([record])
    return render_detail(
        record,
        fmt=fmt,
        columns=columns,
        theme=terminal.theme,
        colorize=should_colorize(terminal.stdout),
        kind=kind,
    )


def _empty_note(terminal: Terminal, text: str) -> None:
    """An empty table's ``info`` line on stderr, with the terminal's own theme and ``quiet``."""
    if terminal.quiet:
        return

    def rendered(color: bool) -> str:
        return render_message("info", text, theme=terminal.theme, colorize=color)

    current_sink().emit(Message(level="info", message=text), rendered=rendered)


def print_styled(
    terminal: Terminal,
    text: Text | str,
    *,
    tail: str = "",
    truncate: bool = False,
) -> None:
    """Print a Rich-styled line to stdout.

    Color is emitted only where the stream supports it (TTY, honoring
    ``NO_COLOR``/``FORCE_COLOR``). Use it for streamed human-readable
    output such as live job events; ``--quiet`` does not mute it.
    ``tail`` follows the styled text verbatim: never wrapped,
    tab-expanded or styled (a streamed log line after a styled
    ``[label] ``). With ``truncate``, each line of ``text`` too wide for
    the terminal ends in an ellipsis instead of wrapping (aligned output
    such as trees). Write stderr through ``ctx.diag.info(text, unmuted=True)``.
    """
    plain = text if isinstance(text, str) else text.plain
    # Rendering may drop trailing spaces: render without them, then restore.
    trailing = plain[len(plain.rstrip()) :] if tail else ""
    if trailing:
        text = text[: len(plain) - len(trailing)]

    def rendered(color: bool) -> str:
        styled = render_styled(text, colorize=color, truncate=truncate) if plain.strip() else ""
        return styled + trailing + tail

    print(rendered(should_colorize(terminal.stdout)), file=terminal.stdout, flush=True)
