"""Semantic UI primitives: prompts, messages, and progress for tool commands."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, TextIO

from untaped.diagnostics import json_diagnostics, write_record
from untaped.errors import ConfigError, OperationCancelledError, UsageError
from untaped.messages import plural
from untaped.progress import ProgressHandle, progress_reporter
from untaped.prompts import (
    PromptBackend,
    PromptChoice,
    PromptToolkitPromptBackend,
    handle_prompt_exception,
    open_controlling_terminal,
    prompt_backend_override,
    prompt_style_from_roles,
)
from untaped.quiet import is_quiet
from untaped.render import (
    MessageKind,
    Renderer,
    RichTerminalRenderer,
    Row,
    render_styled,
    should_colorize,
    stream_is_tty,
)
from untaped.theme import (
    BUILTIN_THEMES,
    OutputFormat,
    ThemeSpec,
    resolve_theme_or_default,
)
from untaped.verbose import is_verbose

if TYPE_CHECKING:
    from rich.text import Text

    from untaped.picker import PickRequest, PickResult


class UiContext:
    """Theme-aware UI context for tool commands."""

    def __init__(
        self,
        *,
        theme: ThemeSpec | None = None,
        renderer: Renderer | None = None,
        prompt_backend: PromptBackend | None = None,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        self.theme = theme or BUILTIN_THEMES["default"]
        self.renderer = renderer or RichTerminalRenderer()
        self.verbose = verbose
        self.quiet = quiet
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self._prompt_backend = prompt_backend
        self._default_prompt_backend: PromptToolkitPromptBackend | None = None

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

        Constructing the default backend imports ``prompt_toolkit``; deferring
        it here keeps that cost off any rendering-only or piped invocation that
        never prompts. An injected backend (tests, alternative frontends) is
        returned as-is. The default is cached only while its input and error
        streams remain the same, so reopened terminals never reuse closed streams.
        A ContextVar override (installed by the test harness
        via ``untaped.testing``) wins over the lazy default so scripted prompts
        reach contexts the test never constructed itself.
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

        backend = PromptToolkitPromptBackend(
            stdin=self.stdin,
            stderr=self.stderr,
            style=prompt_style_from_roles(self.theme.color_roles),
        )
        self._default_prompt_backend = backend
        return backend

    def collection(
        self,
        rows: Sequence[Row],
        *,
        fmt: OutputFormat,
        columns: list[str] | None = None,
        empty: str | bool | None = None,
        kind: str | None = None,
    ) -> str:
        rendered = self.renderer.render_collection(
            rows,
            fmt=fmt,
            columns=columns,
            theme=self.theme,
            colorize=should_colorize(self.stdout),
            kind=kind,
        )
        if not rows and fmt == "table" and empty:
            self.message("info", empty if isinstance(empty, str) else "No results.")
        return rendered

    def detail(
        self,
        record: Row,
        *,
        fmt: OutputFormat,
        columns: list[str] | None = None,
        kind: str | None = None,
    ) -> str:
        return self.renderer.render_detail(
            record,
            fmt=fmt,
            columns=columns,
            theme=self.theme,
            colorize=should_colorize(self.stdout),
            kind=kind,
        )

    def message(self, kind: MessageKind, text: str) -> None:
        """Print a ``success``/``info``/``warning``/``error`` line to stderr.

        ``--quiet`` mutes ``success`` and ``info``. Under JSON diagnostics
        the line is ``{"level": kind, "message": text}``.
        """
        if self.quiet and kind in ("success", "info"):
            return
        if json_diagnostics():
            write_record({"level": kind, "message": text}, self.stderr)
            return
        rendered = self.renderer.render_message(
            kind,
            text,
            theme=self.theme,
            colorize=should_colorize(self.stderr),
        )
        print(rendered, file=self.stderr)

    def success(self, text: str) -> None:
        """Print a success line to stderr (muted by ``--quiet``)."""
        self.message("success", text)

    def styled(
        self, text: Text | str, *, err: bool = False, tail: str = "", truncate: bool = False
    ) -> None:
        """Print a Rich-styled line to stdout, or to stderr with ``err``.

        Color is emitted only where the stream supports it (TTY, honoring
        ``NO_COLOR``/``FORCE_COLOR``). Use it for streamed human-readable
        output such as live job events; unlike :meth:`message`, ``--quiet``
        does not mute it. ``tail`` follows the styled text verbatim: never
        wrapped, tab-expanded or styled (a streamed log line after a styled
        ``[label] ``). With ``truncate``, each line of ``text`` too wide for
        the terminal ends in an ellipsis instead of wrapping (aligned output
        such as trees). Under JSON diagnostics a stderr line is an ``info``
        JSON line.
        """
        stream = self.stderr if err else self.stdout
        plain = text if isinstance(text, str) else text.plain
        if err and json_diagnostics():
            write_record({"level": "info", "message": plain + tail}, stream)
            return
        # Rendering may drop trailing spaces: render without them, then restore.
        trailing = plain[len(plain.rstrip()) :] if tail else ""
        if trailing:
            text = text[: len(plain) - len(trailing)]
        styled = (
            render_styled(text, colorize=should_colorize(stream), truncate=truncate)
            if plain.strip()
            else ""
        )
        print(styled + trailing + tail, file=stream, flush=True)

    def progress(self, label: str) -> AbstractContextManager[ProgressHandle]:
        """Report progress for a blocking operation on stderr.

        TTY renders an animated spinner; non-TTY emits throttled lines; under
        ``verbose`` the wrapped tool's own output streams through. stdout stays
        untouched so piped data is never polluted. Under JSON diagnostics it
        is silent, like ``--quiet``, so stderr stays JSON Lines.
        """
        return progress_reporter(
            label,
            stream=self.stderr,
            verbose=self.verbose,
            quiet=self.quiet or json_diagnostics(),
            isatty=stream_is_tty(self.stderr),
        )

    def confirm(self, message: str, *, default: bool = False) -> bool:
        """Prompt for a yes/no response."""
        self._ensure_promptable()
        try:
            return self.prompt_backend.confirm(message, default=default)
        except (ConfigError, EOFError, KeyboardInterrupt) as exc:
            raise handle_prompt_exception(exc) from exc

    def confirm_action(
        self,
        message: str,
        *,
        assume_yes: bool = False,
        default: bool = False,
        refusal: str = "confirmation requires --yes when not interactive",
    ) -> bool:
        """Confirm a destructive or remote action, reaching the terminal if needed.

        ``assume_yes`` (``--yes``) skips the prompt. Otherwise the prompt
        reads from stdin when it is a TTY, or from the controlling terminal
        when stdin carries piped data. With no terminal at all it raises
        :class:`UsageError` (exit 2) with ``refusal``. Returns the answer;
        on ``False`` the caller raises :class:`OperationCancelledError`
        (or lets :func:`untaped.batch.finish` do it) after printing nothing
        else.
        """
        if assume_yes:
            return True
        with self.terminal(refusal=refusal):
            return self.confirm(message, default=default)

    def confirm_or_cancel(
        self,
        message: str,
        *,
        assume_yes: bool = False,
        refusal: str = "confirmation requires --yes when not interactive",
        preview: Callable[[], None] | None = None,
    ) -> None:
        """:meth:`confirm_action` that raises :class:`OperationCancelledError` on a decline.

        ``preview`` (optional) prints what is about to happen once a terminal
        is secured, right before the prompt; ``assume_yes`` skips both.
        """
        if assume_yes:
            return
        with self.terminal(refusal=refusal):
            if preview is not None:
                preview()
            if not self.confirm(message):
                raise OperationCancelledError

    @contextmanager
    def terminal(
        self, *, refusal: str = "interactive input requires a terminal"
    ) -> Iterator[UiContext]:
        """Point prompts at a terminal for the duration of the block.

        A TTY stdin is used as-is; piped stdin is swapped for the controlling
        terminal (``/dev/tty``) and restored afterwards. Raises
        :class:`UsageError` with ``refusal`` when no terminal is available.
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

    def text(
        self,
        message: str,
        *,
        default: str | None = None,
        required: bool = True,
    ) -> str:
        """Prompt for visible text."""
        self._ensure_promptable()
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
        self._ensure_promptable()
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
        self._ensure_promptable()
        self._validate_choices(choices)
        try:
            return self.prompt_backend.select(message, choices, default=default, search=search)
        except (ConfigError, EOFError, KeyboardInterrupt) as exc:
            raise handle_prompt_exception(exc) from exc

    def multiselect[T](
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        defaults: Sequence[T] | None = None,
        min_count: int = 0,
    ) -> list[T]:
        """Prompt for multiple typed choices."""
        self._ensure_promptable()
        self._validate_choices(choices)
        selected_defaults = list(defaults or ())
        try:
            values = self.prompt_backend.multiselect(
                message,
                choices,
                defaults=selected_defaults,
            )
        except (ConfigError, EOFError, KeyboardInterrupt) as exc:
            raise handle_prompt_exception(exc) from exc
        if len(values) < min_count:
            raise ConfigError(f"select at least {plural(min_count, 'value')}", category="invalid")
        return values

    def pick_many(self, request: PickRequest) -> PickResult:
        """Run the two-pane multi-select picker.

        Raises :class:`OperationCancelledError` when the user quits, and
        :class:`UsageError` without a TTY on stdin.
        """
        self._ensure_promptable()
        ids = [item.id for item in request.catalog.items]
        if len(set(ids)) != len(ids):
            raise ConfigError(
                "picker items must have unique ids", category="failed", system="untaped"
            )
        try:
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


def ui_context(
    *,
    theme: ThemeSpec | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    strict: bool = True,
) -> UiContext:
    """Build a UI context from the built-in theme presets.

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
        from untaped.settings import load_settings_section  # noqa: PLC0415

        theme = resolve_theme_or_default(lambda: load_settings_section("ui"), strict=strict)
    return UiContext(
        theme=theme,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        verbose=is_verbose(),
        quiet=is_quiet(),
    )
