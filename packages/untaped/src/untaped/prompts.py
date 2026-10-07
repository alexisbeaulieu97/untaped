"""Typed prompt primitives: the backend protocol and the screen-backed default."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TextIO, TypeVar

from untaped.errors import ConfigError, PromptInterruptedError, UntapedError

if TYPE_CHECKING:
    from untaped.picker import PickRequest, PickResult
    from untaped.screen.core import Cancel, Quit, Screen
    from untaped.theme import ThemeSpec


T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


@dataclass(frozen=True)
class PromptChoice[T_co]:
    """One typed choice for selection prompts."""

    value: T_co
    label: str
    description: str | None = None


class PromptBackend(Protocol):
    """Backend boundary for interactive prompt implementations.

    A backend may also set ``needs_terminal = False`` (an optional attribute,
    read with a default of ``True``) to say it never draws on a terminal, so
    :class:`UiContext` does not look for one before handing it a prompt, a screen
    (:meth:`UiContext.run`) or a request (:meth:`UiContext.pick_many`).
    """

    def confirm(self, message: str, *, default: bool) -> bool: ...

    def text(self, message: str, *, default: str | None) -> str: ...

    def secret(self, message: str, *, confirmation: bool) -> str: ...

    def select(
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        default: T | None,
        search: bool,
    ) -> T: ...

    def multiselect(
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        defaults: Sequence[T],
    ) -> list[T]: ...

    def pick_many(self, request: PickRequest) -> PickResult | None: ...

    def run_screen[M, R](self, screen: Screen[M, R], *, theme: ThemeSpec) -> Quit[R] | Cancel:
        """Run ``screen`` and return how it ended (``ui.run`` turns that into a result)."""
        ...


_backend_override: ContextVar[PromptBackend | None] = ContextVar(
    "untaped_prompt_backend_override", default=None
)


def prompt_backend_override() -> PromptBackend | None:
    """The invocation-scoped prompt-backend override, if any (test harness)."""
    return _backend_override.get()


def set_prompt_backend_override(
    backend: PromptBackend | None,
) -> Token[PromptBackend | None]:
    """Install a prompt-backend override; returns the token for reset."""
    return _backend_override.set(backend)


def reset_prompt_backend_override(token: Token[PromptBackend | None]) -> None:
    """Undo :func:`set_prompt_backend_override`."""
    _backend_override.reset(token)


_CONTROLLING_TERMINAL = "/dev/tty"

_terminal_override: ContextVar[Callable[[], TextIO] | None] = ContextVar(
    "untaped_terminal_override", default=None
)


def open_controlling_terminal(*, write: bool = False) -> TextIO:
    """Open the process's controlling terminal for prompting.

    Used when stdin carries piped data, so a confirmation can still reach
    the user; opened read-only, or with ``write=True`` for drawing on it (a
    terminal cannot be opened ``r+``, so a screen opens it twice). Raises
    :class:`OSError` when there is no controlling terminal (CI, cron, detached
    sessions). The test harness installs an override via
    :func:`set_terminal_override`, whose handle serves both modes, so tests
    never touch the real terminal.
    """
    override = _terminal_override.get()
    if override is not None:
        return override()
    if write:
        # Never ``O_CREAT``: where there is no terminal this must fail, not create a file.
        return os.fdopen(os.open(_CONTROLLING_TERMINAL, os.O_WRONLY), "w", encoding="utf-8")
    return open(_CONTROLLING_TERMINAL, encoding="utf-8")


def set_terminal_override(
    opener: Callable[[], TextIO] | None,
) -> Token[Callable[[], TextIO] | None]:
    """Install a controlling-terminal opener override; returns the reset token."""
    return _terminal_override.set(opener)


def reset_terminal_override(token: Token[Callable[[], TextIO] | None]) -> None:
    """Undo :func:`set_terminal_override`."""
    _terminal_override.reset(token)


class PromptToolkitPromptBackend:
    """The interactive backend: every prompt is a screen run on the terminal streams.

    Text, secret, select, multiselect and confirm are the inline screens of
    :mod:`untaped.screen.prompts`, and :meth:`pick_many` the picker's full-screen
    one, all run by :meth:`run_screen`. How a screen ends maps onto the line
    prompts' exceptions: an interrupt raises :class:`KeyboardInterrupt` (exit
    130 through ``UiContext``) and a cancel raises :class:`EOFError` (exit 1).
    """

    def __init__(
        self,
        *,
        stdin: TextIO | None = None,
        stderr: TextIO | None = None,
        theme: ThemeSpec | None = None,
    ) -> None:
        self.stdin = stdin or sys.stdin
        self.stderr = stderr or sys.stderr
        self.theme = theme

    def confirm(self, message: str, *, default: bool) -> bool:
        from untaped.screen.prompts import confirm_screen  # noqa: PLC0415

        answer = self._answer(confirm_screen(message, default))
        self._record(f"{message} {'[Y/n]' if default else '[y/N]'}", "y" if answer else "n")
        return answer

    def text(self, message: str, *, default: str | None) -> str:
        from untaped.screen.prompts import text_screen  # noqa: PLC0415

        answer = self._answer(text_screen(message, default))
        self._record(message, answer)
        return answer

    def secret(self, message: str, *, confirmation: bool) -> str:
        from untaped.screen.prompts import secret_screen  # noqa: PLC0415

        value, repeated = self._answer(secret_screen(message, confirmation=confirmation))
        # The record is the mask, one symbol per character as the old line prompt left it.
        mask = self._mask()
        self._record(message, mask * len(value.get_secret_value()))
        if confirmation:
            self._record("Confirm value", mask * len(repeated.get_secret_value()))
        if confirmation and value.get_secret_value() != repeated.get_secret_value():
            raise ConfigError("prompt values did not match", category="invalid")
        return value.get_secret_value()

    def select(
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        default: T | None,
        search: bool,
    ) -> T:
        from untaped.screen.prompts import select_screen  # noqa: PLC0415

        # The screen answers with a row's position, so the record names the row chosen even
        # when two rows share a value.
        default_row = next((i for i, item in enumerate(choices) if item.value == default), None)
        index = self._answer(select_screen(message, choices, default_row, search=search))
        self._record(message, choices[index].label)
        return choices[index].value

    def multiselect(
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        defaults: Sequence[T],
    ) -> list[T]:
        from untaped.screen.prompts import multiselect_screen  # noqa: PLC0415

        checked = [i for i, item in enumerate(choices) if item.value in defaults]
        picked = self._answer(
            multiselect_screen(message, choices, checked), cancelled=_cancelled_error
        )
        self._record(message, ", ".join(choices[index].label for index in picked))
        return [choices[index].value for index in picked]

    def pick_many(self, request: PickRequest) -> PickResult | None:
        """Run the two-pane picker as a screen on this backend's terminal streams.

        Returns ``None`` when the user cancelled; an interrupt raises
        :class:`KeyboardInterrupt`, which ``UiContext`` maps like every prompt.
        """
        from untaped.picker.screen import picker_screen  # noqa: PLC0415
        from untaped.screen.core import Quit  # noqa: PLC0415

        outcome = self.run_screen(picker_screen(request), theme=self._theme())
        if isinstance(outcome, Quit):
            return outcome.result
        if outcome.interrupted:
            raise KeyboardInterrupt
        return None

    def run_screen[M, R](self, screen: Screen[M, R], *, theme: ThemeSpec) -> Quit[R] | Cancel:
        """Run ``screen`` on this backend's terminal streams (input stdin, drawing on stderr)."""
        from untaped.screen.terminal import run_screen_on  # noqa: PLC0415

        return run_screen_on(screen, stdin=self.stdin, stderr=self.stderr, theme=theme)

    def _theme(self) -> ThemeSpec:
        from untaped.theme import BUILTIN_THEMES  # noqa: PLC0415

        return self.theme or BUILTIN_THEMES["default"]

    def _mask(self) -> str:
        from untaped.theme import DEFAULT_SYMBOLS  # noqa: PLC0415

        symbols = self._theme().symbols
        return symbols.get("mask") or DEFAULT_SYMBOLS["mask"]

    def _record(self, question: str, answer: str) -> None:
        """Print ``<question>: <answer>`` so the scrollback keeps what the erased prompt showed.

        Plain text on the stream the prompt drew on, never through markup; control
        characters are dropped so an answer cannot move the cursor or recolour the terminal.
        Only an answered prompt records: a cancel or an interrupt raises before this runs.
        """
        line = f"{question}: {answer}"
        self.stderr.write("".join(ch for ch in line if ch.isprintable()) + "\n")
        self.stderr.flush()

    def _answer[M, R](
        self, screen: Screen[M, R], *, cancelled: Callable[[], BaseException] = EOFError
    ) -> R:
        """Run a prompt screen and return its answer, or raise how the line prompts ended.

        ``cancelled`` builds the exception for a user who ended it without an answer
        (``EOFError``, like ctrl-d on a line prompt); an interrupt is always
        :class:`KeyboardInterrupt`.
        """
        from untaped.screen.core import Quit  # noqa: PLC0415

        outcome = self.run_screen(screen, theme=self._theme())
        if isinstance(outcome, Quit):
            return outcome.result
        if outcome.interrupted:
            raise KeyboardInterrupt
        raise cancelled()


def _cancelled_error() -> ConfigError:
    """What a prompt ended without an answer is: ``prompt cancelled``, exit 1."""
    return ConfigError("prompt cancelled", category="failed", system="untaped")


def handle_prompt_exception(exc: BaseException) -> UntapedError:
    """Convert terminal prompt cancellation into a user-facing error.

    Ctrl-D (``EOFError``) cancels with exit ``1``; Ctrl-C becomes a
    :class:`PromptInterruptedError`, which exits ``130`` like any interrupt.
    """
    if isinstance(exc, KeyboardInterrupt):
        return PromptInterruptedError("prompt cancelled")
    if isinstance(exc, EOFError):
        return _cancelled_error()
    if isinstance(exc, ConfigError):
        return exc
    raise exc
