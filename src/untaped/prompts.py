"""Typed prompt primitives backed by prompt_toolkit."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TextIO, TypeVar

from untaped.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.formatted_text import AnyFormattedText
    from prompt_toolkit.styles import Style


T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


@dataclass(frozen=True)
class PromptChoice[T_co]:
    """One typed choice for selection prompts."""

    value: T_co
    label: str
    description: str | None = None


class PromptBackend(Protocol):
    """Backend boundary for interactive prompt implementations."""

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


class PromptToolkitPromptBackend:
    """prompt_toolkit-backed implementation for interactive terminals."""

    def __init__(
        self,
        *,
        stdin: TextIO | None = None,
        stderr: TextIO | None = None,
        style: Style | None = None,
    ) -> None:
        self.stdin = stdin or sys.stdin
        self.stderr = stderr or sys.stderr
        self.style = style

    def confirm(self, message: str, *, default: bool) -> bool:
        suffix = " [Y/n]: " if default else " [y/N]: "
        default_text = "y" if default else "n"
        while True:
            answer = self._prompt(f"{message}{suffix}", default=default_text).strip().lower()
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            self.stderr.write("Please answer y or n.\n")
            self.stderr.flush()

    def text(self, message: str, *, default: str | None) -> str:
        return self._prompt(f"{message}: ", default=default or "")

    def secret(self, message: str, *, confirmation: bool) -> str:
        value = self._prompt(f"{message}: ", default="", is_password=True)
        if not confirmation:
            return value
        repeated = self._prompt("Confirm value: ", default="", is_password=True)
        if value != repeated:
            raise ConfigError("prompt values did not match")
        return value

    def select(
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        default: T | None,
        search: bool,
    ) -> T:
        if search:
            return self._search_select(message, choices, default=default)
        from prompt_toolkit.shortcuts import choice  # noqa: PLC0415

        options = [
            (index, _choice_display(choice_item)) for index, choice_item in enumerate(choices)
        ]
        default_index = _choice_index(choices, default)
        with self._app_session():
            selected_index = choice(
                message=f"{message}:",
                options=options,
                default=default_index,
                style=self.style,
            )
        return choices[selected_index].value

    def multiselect(
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        defaults: Sequence[T],
    ) -> list[T]:
        from prompt_toolkit.shortcuts import checkboxlist_dialog  # noqa: PLC0415

        values = [
            (index, _choice_display(choice_item)) for index, choice_item in enumerate(choices)
        ]
        default_indexes = [
            index for index, choice_item in enumerate(choices) if choice_item.value in defaults
        ]
        with self._app_session():
            selected_indexes = checkboxlist_dialog(
                title="Select",
                text=message,
                values=values,
                default_values=default_indexes,
                style=self.style,
            ).run()
        if selected_indexes is None:
            raise ConfigError("prompt cancelled")
        return [choices[index].value for index in selected_indexes]

    def _search_select(
        self,
        message: str,
        choices: Sequence[PromptChoice[T]],
        *,
        default: T | None,
    ) -> T:
        from prompt_toolkit.completion import WordCompleter  # noqa: PLC0415

        labels = [_choice_display(choice_item) for choice_item in choices]
        by_label = dict(zip(labels, choices, strict=True))
        default_index = _choice_index(choices, default)
        default_label = labels[default_index] if default_index is not None else ""
        selected_label = self._prompt(
            f"{message}: ",
            default=default_label,
            completer=WordCompleter(labels, ignore_case=True, sentence=True),
        )
        choice_item = by_label.get(selected_label)
        if choice_item is None:
            raise ConfigError(f"invalid selection: {selected_label}")
        return choice_item.value

    def _prompt(
        self,
        message: str,
        *,
        default: str,
        is_password: bool = False,
        completer: WordCompleter | None = None,
    ) -> str:
        from prompt_toolkit import PromptSession  # noqa: PLC0415
        from prompt_toolkit.input.defaults import create_input  # noqa: PLC0415
        from prompt_toolkit.output.defaults import create_output  # noqa: PLC0415

        session: PromptSession[str] = PromptSession(
            input=create_input(self.stdin),
            output=create_output(self.stderr),
            style=self.style,
            completer=completer,
        )
        rendered_message: AnyFormattedText = [("class:prompt", message)]
        return session.prompt(rendered_message, default=default, is_password=is_password)

    @contextmanager
    def _app_session(self) -> Iterator[None]:
        from prompt_toolkit.application.current import create_app_session  # noqa: PLC0415
        from prompt_toolkit.input.defaults import create_input  # noqa: PLC0415
        from prompt_toolkit.output.defaults import create_output  # noqa: PLC0415

        with create_app_session(
            input=create_input(self.stdin),
            output=create_output(self.stderr),
        ):
            yield


def prompt_style_from_roles(color_roles: dict[str, str]) -> Style:
    """Build a prompt_toolkit style from conservative UI color roles."""
    from prompt_toolkit.styles import Style  # noqa: PLC0415

    key = _prompt_toolkit_style(color_roles.get("key") or color_roles.get("header"))
    value = _prompt_toolkit_style(color_roles.get("value"))
    border = _prompt_toolkit_style(color_roles.get("border"))
    return Style.from_dict(
        {
            "prompt": key,
            "input-selection": value,
            "selected-option": value,
            "frame.border": border,
            "dialog.body": "",
        }
    )


def handle_prompt_exception(exc: BaseException) -> ConfigError:
    """Convert terminal prompt cancellation into a user-facing config error."""
    if isinstance(exc, (EOFError, KeyboardInterrupt)):
        return ConfigError("prompt cancelled")
    if isinstance(exc, ConfigError):
        return exc
    raise exc


def _choice_display[T](choice_item: PromptChoice[T]) -> str:
    if choice_item.description:
        return f"{choice_item.label} - {choice_item.description}"
    return choice_item.label


def _choice_index[T](choices: Sequence[PromptChoice[T]], value: T | None) -> int | None:
    if value is None:
        return None
    return next(
        (index for index, choice_item in enumerate(choices) if choice_item.value == value),
        None,
    )


def _prompt_toolkit_style(style: str | None) -> str:
    if not style:
        return ""
    tokens = [_STYLE_TOKENS.get(token, "") for token in style.split()]
    return " ".join(token for token in tokens if token)


_STYLE_TOKENS = {
    "bold": "bold",
    "dim": "",
    "red": "ansired",
    "green": "ansigreen",
    "yellow": "ansiyellow",
    "blue": "ansiblue",
    "magenta": "ansimagenta",
    "cyan": "ansicyan",
    "white": "ansigray",
    "bright_red": "ansibrightred",
    "bright_green": "ansibrightgreen",
    "bright_yellow": "ansibrightyellow",
    "bright_blue": "ansibrightblue",
    "bright_magenta": "ansibrightmagenta",
    "bright_cyan": "ansibrightcyan",
    "bright_white": "ansiwhite",
}
