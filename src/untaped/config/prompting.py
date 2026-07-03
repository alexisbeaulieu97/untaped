"""Prompt/stdin/value resolution helpers for ``config set``."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Literal, get_args, get_origin

from untaped.cli import raise_usage
from untaped.config.ports import SettingsReader
from untaped.config.use_cases import GetSetting
from untaped.config_schema import FieldDescriptor
from untaped.errors import ConfigError
from untaped.prompts import PromptChoice
from untaped.theme import BUILTIN_THEMES
from untaped.ui import ui_context


def resolve_set_value(
    full_key: str,
    value: str | None,
    *,
    stdin: bool,
    prompt: bool,
    repo: SettingsReader,
    target_profile: str | None,
) -> str:
    choices = (("VALUE", value is not None), ("--stdin", stdin), ("--prompt", prompt))
    sources = [name for name, selected in choices if selected]
    if not sources:
        raise_usage("provide VALUE, --stdin, or --prompt")
    if len(sources) > 1:
        raise_usage("provide only one of VALUE, --stdin, or --prompt")
    if stdin:
        return _read_stdin_value()
    if prompt:
        return _prompt_value(full_key, repo, target_profile=target_profile)
    assert value is not None
    return value


def _read_stdin_value() -> str:
    if sys.stdin.isatty():
        raise ConfigError("no value received on stdin")
    value = sys.stdin.read().rstrip("\r\n")
    if "\n" in value or "\r" in value:
        raise ConfigError("--stdin expects exactly one value")
    if not value.strip():
        raise ConfigError("no value received on stdin")
    return value


def _prompt_value(full_key: str, repo: SettingsReader, *, target_profile: str | None) -> str:
    descriptor = repo.descriptor(full_key)
    message = f"Value for {full_key}"
    ui = ui_context(strict=False)
    if descriptor.is_secret:
        return ui.secret(message)
    default = _prompt_default(full_key, descriptor, repo, target_profile=target_profile)
    if full_key == "ui.theme":
        return _raw_prompt_scalar(
            ui.select(message, _theme_choices(), default=default, search=True)
        )
    literal_values = _literal_values(descriptor)
    if literal_values:
        return _raw_prompt_scalar(
            ui.select(
                message,
                [PromptChoice(value=item, label=str(item)) for item in literal_values],
                default=_default_choice(default, literal_values),
            )
        )
    if descriptor.annotation is bool:
        bool_values = ["true", "false"]
        return ui.select(
            message,
            [PromptChoice(value=item, label=item) for item in bool_values],
            default=default if default in bool_values else None,
        )
    return ui.text(message, default=default)


def _prompt_default(
    full_key: str,
    descriptor: FieldDescriptor,
    repo: SettingsReader,
    *,
    target_profile: str | None,
) -> str | None:
    from untaped.config.models import display_default, display_value  # noqa: PLC0415

    entry = GetSetting(repo)(full_key)
    value = str(entry.value)
    if target_profile is not None and entry.source.kind != "env":
        scoped = repo.profile_value_for(descriptor, target_profile)
        value = (
            display_default(descriptor)
            if scoped is None
            else display_value(descriptor, scoped, reveal_secrets=False)
        )
    if value in {"", "—", "***"}:
        return None
    if descriptor.annotation is bool:
        return value.lower()
    return value


def _literal_values(descriptor: FieldDescriptor) -> list[object]:
    if get_origin(descriptor.annotation) is Literal:
        return list(get_args(descriptor.annotation))
    return []


def _theme_choices() -> list[PromptChoice[str]]:
    return [PromptChoice(value=name, label=name) for name in sorted(BUILTIN_THEMES)]


def _default_choice(default: str | None, choices: Sequence[object]) -> object | None:
    if default is None:
        return None
    return next((choice for choice in choices if str(choice) == default), None)


def _raw_prompt_scalar(value: object) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)
