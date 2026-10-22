"""A service's connection in ``untaped setup``: its form, and what the form asks to write.

A service's marked settings (:func:`~untaped.management.setup_state.form_fields`)
and its token are one row of the setup screen, edited together in this form
(a text field per marked setting, the token source as tabs, ``Save``) and
checked online before anything is written
(:mod:`untaped.management.setup_screen`). The form's values become a
:class:`~untaped.management.setup_write.Candidate`, which
:func:`~untaped.management.setup_write.write_candidate` writes.

The token choices name no plain-text storage when a store or a command can
hold the token. A typed token lives in a ``SecretInput`` and a ``Candidate``
as a ``SecretStr``, never in plain text here.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from pydantic import SecretStr

from untaped.auth import takes_token_command, token_env_names
from untaped.credentials import Credential
from untaped.errors import ConfigError
from untaped.management.setup_state import ServiceState, form_fields, service_marks
from untaped.management.setup_write import Candidate
from untaped.messages import hint
from untaped.plugins.registry import PluginSpec, settings_model
from untaped.profile_resolver import DEFAULT_PROFILE
from untaped.prompts import PromptChoice
from untaped.screen.components.buttons import Button, Buttons
from untaped.screen.components.form import Form
from untaped.screen.components.inputs import SecretInput, TextInput
from untaped.screen.components.tabs import Tab, Tabs
from untaped.token_store import TokenStore, preset_entry

__all__ = [
    "anyway_buttons",
    "candidate",
    "connection_form",
    "reset",
    "token_choices",
    "with_buttons",
]

_TAB_LABELS = {
    "keep": "Keep",
    "move": "Move",
    "command": "Command",
    "env": "Env",
    "enter": "Enter",
}


def connection_form(
    spec: PluginSpec, state: ServiceState, store: TokenStore | None, profile: str
) -> Form:
    """The connection form of ``spec`` in ``profile``: its marked settings, its token, ``Save``."""
    fields: list[tuple[str, Any]] = [
        (
            field.key,
            TextInput(
                _label(field.path[-1], title=True),
                state.values.get(field.key, ""),
                validator=_required(_label(field.path[-1])),
            ),
        )
        for field in form_fields(spec)
    ]
    if service_marks(spec).token is not None:
        has_command = takes_token_command(settings_model(spec))
        choices = token_choices(spec, state, store, has_command=has_command)
        tabs = tuple(
            _tab(spec, state, store, choice, profile, has_command=has_command) for choice in choices
        )
        fields.append(("token", Tabs("Token source", tabs, active=choices[0].value)))
    return Form((*fields, ("buttons", _save_buttons())))


def _tab(
    spec: PluginSpec,
    state: ServiceState,
    store: TokenStore | None,
    choice: PromptChoice[str],
    profile: str,
    *,
    has_command: bool,
) -> Tab:
    how = choice.value
    label = store.name if how == "store" and store is not None else _TAB_LABELS[how]
    note = choice.label
    if how == "keep" and has_command and state.inherited_token:
        note += (
            f"\n{spec.name}.token is set in profile {DEFAULT_PROFILE}: switching "
            f"profile {profile} to a stored token or a command would leave it in charge, so "
            f"only keeping the current token is offered\n{hint('auth migrate')}"
        )
    fields: tuple[tuple[str, Any], ...] = ()
    if how in ("store", "enter"):
        fields = (("token", SecretInput("Token", validator=_token_error)),)
    elif how == "command":
        validator = _command_validator(spec.name)
        fields = (("command", TextInput("Command", validator=validator)),)
    return Tab(how, label, fields, note)


def _save_buttons() -> Buttons:
    return Buttons((Button("save", "Save", "primary"), Button("cancel", "Cancel", "ghost")))


def anyway_buttons() -> Buttons:
    """``Save anyway`` and ``Cancel``: what the form offers once its check failed."""
    return Buttons(
        (Button("save_anyway", "Save anyway", "primary"), Button("cancel", "Cancel", "ghost"))
    )


def with_buttons(form: Form, buttons: Buttons) -> Form:
    """``form`` with ``buttons`` as its buttons."""
    fields = tuple((name, buttons if name == "buttons" else field) for name, field in form.fields)
    return replace(form, fields=fields)


def reset(form: Form) -> Form:
    """``form`` with ``Save`` and ``Cancel`` as its buttons again."""
    return with_buttons(form, _save_buttons())


#: Words a field label writes upper case: ``base_url`` reads "Base URL".
_ACRONYMS = frozenset({"api", "id", "uri", "url"})


def _label(name: str, *, title: bool = False) -> str:
    """A setting's name as words, acronyms upper case: ``base URL`` (``Base URL`` as a title)."""
    text = " ".join(word.upper() if word in _ACRONYMS else word for word in name.split("_"))
    return text[:1].upper() + text[1:] if title else text


def _required(label: str) -> Callable[[str], str]:
    """A validator that refuses an empty value, naming the field: "Enter the base URL."."""

    def check(text: str) -> str:
        return "" if text.strip() else f"Enter the {label}."

    return check


def _token_error(token: SecretStr) -> str:
    return "" if token.get_secret_value().strip() else "Enter a token."


def _command_validator(name: str) -> Callable[[str], str]:
    def validate(text: str) -> str:
        try:
            _token_command(name, text)
        except ConfigError as exc:
            return str(exc)
        return ""

    return validate


def _token_command(name: str, text: str) -> list[str]:
    """The token command's argv; an empty or malformed one is refused before anything is written."""
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise ConfigError(f"invalid {name} token command: {exc}", category="invalid") from exc
    if not argv:
        raise ConfigError(f"{name} token command is empty", category="invalid")
    return argv


def candidate(spec: PluginSpec, profile: str, values: Mapping[str, object]) -> Candidate:
    """The form's validated values as what the user asked for (``keep`` without token tabs)."""
    tab = values.get("token", {"tab": "keep"})
    assert isinstance(tab, dict)
    how = str(tab["tab"])
    token = None
    argv = None
    if how in ("store", "enter"):
        typed = tab["token"]
        assert isinstance(typed, SecretStr)
        token = SecretStr(typed.get_secret_value().strip())
    elif how == "command":
        argv = tuple(_token_command(spec.name, str(tab["command"])))
    fields = tuple((field.key, str(values[field.key]).strip()) for field in form_fields(spec))
    return Candidate(spec.name, spec.name, profile, how, fields, token, argv)


def token_choices(
    spec: PluginSpec,
    current: ServiceState,
    store: TokenStore | None,
    *,
    has_command: bool,
) -> list[PromptChoice[str]]:
    """The token choices, the recommended one first; none stores plain text if avoidable.

    While ``default``'s plaintext token wins, keeping it is the only choice
    that would work for a section ``auth`` serves.
    """
    choices: list[PromptChoice[str]] = []
    if current.token_source is not None:
        label = f"Keep the current token ({current.token_source})"
        choices.append(PromptChoice(value="keep", label=label))
    token = service_marks(spec).token
    credential = None if token is None else token.credential
    if credential is not None:
        # A stored login answers before any setting, default's plain text included.
        return _login_choices(choices, credential, current, store)
    if has_command and current.inherited_token:
        return choices
    if current.plaintext is not None and store is not None:
        label = f"Move the current token to {store.name}"
        choices.insert(0, PromptChoice(value="move", label=label))
    if store is not None:
        choices.append(PromptChoice(value="store", label=f"Store a token with {store.name}"))
    if has_command:
        choices.append(PromptChoice(value="command", label="Run a command that prints the token"))
        env = token_env_names(settings_model(spec).model_construct())
        # default's command would win over the variable once the profile's is gone.
        if env and not current.inherited_command:
            stored = current.own_command is not None and preset_entry(current.own_command)
            effect = "drops the stored token" if stored else "nothing is stored"
            label = f"Use ${env[0]} (you export it; {effect})"
            choices.append(PromptChoice(value="env", label=label))
    else:
        choices.append(PromptChoice(value="enter", label="Enter a token (stored in config.yml)"))
    return choices


def _login_choices(
    choices: list[PromptChoice[str]],
    credential: Credential[Any],
    current: ServiceState,
    store: TokenStore | None,
) -> list[PromptChoice[str]]:
    """A declared credential's choices: each files a login for the key, as ``auth login`` does."""
    if current.plaintext is not None and store is not None:
        label = f"Move the current token to {store.name}"
        choices.insert(0, PromptChoice(value="move", label=label))
    if store is not None:
        choices.append(PromptChoice(value="store", label=f"Store a token with {store.name}"))
    choices.append(PromptChoice(value="command", label="Run a command that prints the token"))
    if credential.env:
        label = f"Use ${credential.env[0]} (you export it; drops a stored token)"
        choices.append(PromptChoice(value="env", label=label))
    return choices
