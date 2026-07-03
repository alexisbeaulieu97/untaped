"""Semantic UI primitives: prompts, messages, and progress for tool commands."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol, TextIO, cast

from untaped.errors import ConfigError
from untaped.progress import ProgressHandle, progress_reporter
from untaped.prompts import (
    PromptBackend,
    PromptChoice,
    handle_prompt_exception,
)
from untaped.render import (
    MessageKind,
    OutputFormat,
    Renderer,
    RichTerminalRenderer,
    Row,
    should_colorize,
    stream_is_tty,
)
from untaped.theme import (
    BUILTIN_THEMES,
    ThemeSpec,
    UiSettings,
    resolve_theme_or_default,
)


class _HasUiSettings(Protocol):
    ui: UiSettings


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

    @property
    def prompt_backend(self) -> PromptBackend:
        """The interactive prompt backend, built lazily on first use.

        Constructing the default backend imports ``prompt_toolkit``; deferring
        it here keeps that cost off any rendering-only or piped invocation that
        never prompts. An injected backend (tests, alternative frontends) is
        returned as-is. A ContextVar override (installed by the test harness
        via ``untaped.testing``) wins over the lazy default so scripted prompts
        reach contexts the test never constructed itself.
        """
        backend = self._prompt_backend
        if backend is not None:
            return backend
        from untaped.prompts import prompt_backend_override  # noqa: PLC0415

        override = prompt_backend_override()
        if override is not None:
            return override
        from untaped.prompts import (  # noqa: PLC0415
            PromptToolkitPromptBackend,
            prompt_style_from_roles,
        )

        backend = PromptToolkitPromptBackend(
            stdin=self.stdin,
            stderr=self.stderr,
            style=prompt_style_from_roles(self.theme.color_roles),
        )
        self._prompt_backend = backend
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
            note = empty if isinstance(empty, str) else "No results."
            print(
                self.renderer.render_message(
                    "info", note, theme=self.theme, colorize=should_colorize(self.stderr)
                ),
                file=self.stderr,
            )
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
        if self.quiet and kind in ("success", "info"):
            return
        rendered = self.renderer.render_message(
            kind,
            text,
            theme=self.theme,
            colorize=should_colorize(self.stderr),
        )
        print(rendered, file=self.stderr)

    def progress(self, label: str) -> AbstractContextManager[ProgressHandle]:
        """Report progress for a blocking operation on stderr.

        TTY renders an animated spinner; non-TTY emits throttled lines; under
        ``verbose`` the wrapped tool's own output streams through. stdout stays
        untouched so piped data is never polluted.
        """
        return progress_reporter(
            label,
            stream=self.stderr,
            verbose=self.verbose,
            quiet=self.quiet,
            isatty=stream_is_tty(self.stderr),
        )

    def confirm(self, message: str, *, default: bool = False) -> bool:
        """Prompt for a yes/no response."""
        self._ensure_promptable()
        try:
            return self.prompt_backend.confirm(message, default=default)
        except (ConfigError, EOFError, KeyboardInterrupt) as exc:
            raise handle_prompt_exception(exc) from exc

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
            raise ConfigError(f"select at least {min_count} value(s)")
        return values

    def _ensure_promptable(self) -> None:
        if not stream_is_tty(self.stdin):
            raise ConfigError("interactive prompt requires a TTY on stdin")

    @staticmethod
    def _validate_prompt_text(value: str, *, required: bool) -> str:
        if required and not value.strip():
            raise ConfigError("no value received from prompt")
        return value

    @staticmethod
    def _validate_choices[T](choices: Sequence[PromptChoice[T]]) -> None:
        if not choices:
            raise ConfigError("prompt requires at least one choice")
        labels = [choice.label for choice in choices]
        if len(set(labels)) != len(labels):
            raise ConfigError("prompt choices must have unique labels")


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
    from untaped.quiet import is_quiet  # noqa: PLC0415
    from untaped.verbose import is_verbose  # noqa: PLC0415

    if theme is None:
        from untaped.settings import get_settings  # noqa: PLC0415

        theme = resolve_theme_or_default(
            lambda: cast(_HasUiSettings, get_settings()).ui, strict=strict
        )
    return UiContext(
        theme=theme,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        verbose=is_verbose(),
        quiet=is_quiet(),
    )
