"""A plugin's setup form in ``untaped setup``, and what the form asks to write.

A plugin's marked settings (:func:`~untaped.core.commands.setup_state.form_fields`)
and its secrets are one row of the setup screen, edited together in this
form (each setting in its shape's field, as its own row would open it, each
secret's source as tabs, ``Save``) and checked online before anything is
written (:mod:`untaped.core.commands.setup_screen`). The form's values become a
:class:`~untaped.core.commands.setup_write.Candidate`, which
:func:`~untaped.core.commands.setup_write.write_candidate` writes in one change.

The source choices never store a secret in plain text. A typed secret lives
in a ``SecretInput`` and a ``Candidate`` as a ``SecretStr``, never in plain
text here.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from untaped.core.commands.setup_state import ServiceState, form_fields, service_marks
from untaped.core.commands.setup_write import Candidate, SecretChoice
from untaped.core.config.fields import editor, written
from untaped.core.config.repository import SettingsFileRepository
from untaped.runtime.registry import settings_model
from untaped.screen.components.buttons import Button, Buttons
from untaped.screen.components.form import Form
from untaped.screen.components.inputs import SecretInput, TextInput
from untaped.screen.components.tabs import Tab, Tabs
from untaped.sdk._schema import FieldDescriptor, walk_settings
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.marks import MarkedField
from untaped.sdk.network._token_store import TokenStore
from untaped.sdk.prompt.prompts import PromptChoice

if TYPE_CHECKING:
    from untaped.runtime.composed import ComposedPlugin

__all__ = [
    "anyway_buttons",
    "candidate",
    "connection_form",
    "reset",
    "token_choices",
    "with_buttons",
]

#: The name of the typed secret's field inside a source tab.
_TYPED = "typed"

_TAB_LABELS = {"keep": "Keep", "command": "Command", "env": "Env"}


def connection_form(spec: ComposedPlugin, state: ServiceState, store: TokenStore | None) -> Form:
    """The setup form of ``spec``: its marked settings, its secrets, ``Save``.

    Each marked setting opens in its shape's field (as its row would); each
    secret gets source tabs.
    """
    descriptors = _descriptors(spec)
    fields: list[tuple[str, Any]] = [
        (field.key, _setting(descriptors[field.path], field, state)) for field in form_fields(spec)
    ]
    for secret in service_marks(spec).secrets:
        choices = token_choices(spec, state, store, secret=secret)
        tabs = tuple(_tab(spec, store, choice, secret) for choice in choices)
        title = f"{_label(secret.key, title=True)} source"
        fields.append((_source(secret), Tabs(title, tabs, active=choices[0].value)))
    return Form((*fields, ("buttons", _save_buttons())))


def _descriptors(spec: ComposedPlugin) -> dict[tuple[str, ...], FieldDescriptor]:
    model = settings_model(spec)
    return {d.path: d for d in walk_settings(model, include_collections=True)}


def _setting(descriptor: FieldDescriptor, field: MarkedField, state: ServiceState) -> Any:
    """``field``'s editor, holding its current value; one the plugin needs refuses empty."""
    label = _label(field.path[-1], title=True)
    component = editor(descriptor, state.values.get(field.key), label=label)
    if field.needs_setup and isinstance(component, TextInput):
        component = replace(component, validator=_required(_label(field.path[-1])))
    return component


def _source(secret: MarkedField) -> str:
    """The form's name for ``secret``'s source tabs (never a setting's key: it has a colon)."""
    return f"{secret.key}:source"


def _noun(secret: MarkedField) -> str:
    """How the form names ``secret``: ``token``, ``webhook secret``."""
    return secret.key.replace("_", " ")


def _tab(
    spec: ComposedPlugin, store: TokenStore | None, choice: PromptChoice[str], secret: MarkedField
) -> Tab:
    how = choice.value
    label = store.name if how == "store" and store is not None else _TAB_LABELS[how]
    noun = _noun(secret)
    fields: tuple[tuple[str, Any], ...] = ()
    if how == "store":
        typed = SecretInput(noun[:1].upper() + noun[1:], validator=_secret_error(noun))
        fields = ((_TYPED, typed),)
    elif how == "command":
        validator = _command_validator(spec.name)
        fields = (("command", TextInput("Command", validator=validator)),)
    return Tab(how, label, fields, choice.label)


def _save_buttons() -> Buttons:
    return Buttons((Button("save", "Save", "primary"), Button("cancel", "Cancel", "ghost")))


def anyway_buttons() -> Buttons:
    """``Save anyway`` and ``Cancel`` (focused): what the form offers once its check failed."""
    save = Button("save_anyway", "Save anyway", "primary")
    cancel = Button("cancel", "Cancel", "ghost")
    return Buttons((save, cancel), focus=1)  # a reflexive enter never saves what failed


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


def _secret_error(noun: str) -> Callable[[SecretStr], str]:
    def check(secret: SecretStr) -> str:
        return "" if secret.get_secret_value().strip() else f"Enter a {noun}."

    return check


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


def candidate(spec: ComposedPlugin, profile: str, form: Form, state: ServiceState) -> Candidate:
    """The form's validated values as what the user asked for.

    ``state`` is the one the form was built from: only the settings the user
    changed from it are written, each expecting the profile's value it held,
    so a value the profile inherits stays inherited and one changed since
    is not replaced; a secret setting left empty keeps its value. Raises
    :class:`ConfigError` for a value ``config set`` would refuse, or a
    setting the plugin needs left empty, its ``details["field"]`` naming
    the form's field.
    """
    descriptors = _descriptors(spec)
    held = dict(form.fields)
    repo = SettingsFileRepository()
    values: list[tuple[str, str | None]] = []
    for field in form_fields(spec):
        descriptor = descriptors[field.path]
        text = _written(descriptor, held[field.key])
        opened = _written(descriptor, editor(descriptor, state.values.get(field.key), label=""))
        if text is None and descriptor.is_secret and state.values.get(field.key) is not None:
            continue  # an empty secret keeps the one set
        refused = {"field": field.key}  # the screen shows the refusal on this field
        if text is None and field.needs_setup:
            label = _label(field.path[-1])
            raise ConfigError(f"enter the {label}", category="invalid", details=refused)
        if text == opened:
            continue
        if text is not None:
            text = text.strip() if descriptor.annotation is not SecretStr else text
            try:
                repo.coerced(f"{spec.name}.{field.key}", text)  # refuses what config set would
            except ConfigError as exc:
                raise ConfigError(str(exc), category="invalid", details=refused) from exc
        values.append((field.key, text))
    secrets = tuple(
        _choice(spec.name, secret, held[_source(secret)].value)
        for secret in service_marks(spec).secrets
    )
    expected = tuple((key, state.own.get(key)) for key, _ in values)
    return Candidate(spec.name, spec.name, profile, tuple(values), secrets, expected)


def _written(descriptor: FieldDescriptor, component: Any) -> str | None:
    """What ``component`` writes, an empty list as nothing (a list the plugin needs is refused)."""
    text = written(descriptor, component)
    return None if text == "[]" else text


def _choice(name: str, secret: MarkedField, tab: object) -> SecretChoice:
    """One secret's tab as what the user asked for (``keep`` with no tabs)."""
    assert isinstance(tab, dict)
    how = str(tab["tab"])
    if how == "store":
        typed = tab[_TYPED]
        assert isinstance(typed, SecretStr)
        return SecretChoice(secret.key, how, token=SecretStr(typed.get_secret_value().strip()))
    if how == "command":
        return SecretChoice(secret.key, how, argv=tuple(_token_command(name, str(tab["command"]))))
    return SecretChoice(secret.key, how)


def token_choices(
    spec: ComposedPlugin,
    current: ServiceState,
    store: TokenStore | None,
    *,
    secret: MarkedField | None = None,
) -> list[PromptChoice[str]]:
    """A secret's source choices, the recommended one first: each files a login, as ``auth login``.

    ``secret`` defaults to the one a bare section names.
    """
    marks = service_marks(spec)
    secret = secret or marks.default_secret or marks.secrets[0]
    noun = _noun(secret)
    choices: list[PromptChoice[str]] = []
    source = current.secret_sources.get(secret.key)
    if source is not None:
        label = f"Keep the current {noun} ({source})"
        choices.append(PromptChoice(value="keep", label=label))
    if store is not None:
        choices.append(PromptChoice(value="store", label=f"Store a {noun} with {store.name}"))
    choices.append(PromptChoice(value="command", label=f"Run a command that prints the {noun}"))
    credential = secret.credential
    if credential is not None and credential.env:
        label = f"Use ${credential.env[0]} (you export it; drops a stored {noun})"
        choices.append(PromptChoice(value="env", label=label))
    return choices
