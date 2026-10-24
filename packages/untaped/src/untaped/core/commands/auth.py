"""Root ``untaped auth …`` command group: the credentials plugins declare.

``auth login`` files a secret for a declared
:class:`~untaped.sdk.network.credentials.Credential` and the key it is for (this run's
server), in this machine's password store or as a command that prints it;
``auth list`` shows every stored one; ``auth forget`` deletes one;
``auth status`` says, for this run, which source answers each credential,
without reading anything. Secrets are read from a hidden prompt or stdin,
never from an argument.

``auth set``, ``auth unset`` and ``auth migrate`` keep their 11.0 meaning
(a section's ``token_command`` in the config file) and are deprecated:
``auth login`` and ``auth forget`` replace them.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from untaped.core.commands.records import (
    AuthOutcome,
    CredentialOutcome,
    StoredCredential,
    TokenSource,
)
from untaped.core.config.repository import SettingsFileRepository
from untaped.core.settings import CoreSettings
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.profile_resolver import (
    DEFAULT_PROFILE,
    resolve_profiles,
    selected_profile,
    write_target,
)
from untaped.runtime.registry import CompositionResult, settings_model
from untaped.runtime.settings import active_settings_layout, load_settings_section
from untaped.runtime.sink import Diagnostics
from untaped.sdk._stdin import read_stdin_text
from untaped.sdk._ui import UiContext, ui_context
from untaped.sdk.diagnostic import Failure
from untaped.sdk.errors import ConfigError, UsageError, first_validation_error, most_severe
from untaped.sdk.fields import Confirm, Output, Positional
from untaped.sdk.marks import deprecated
from untaped.sdk.network._credential_store import (
    CredentialRow,
    find_row,
    ready_store,
    rows,
    rows_by_prefix,
    store_token,
)
from untaped.sdk.network._token_store import (
    StoreName,
    TokenStore,
    entry_name,
    no_store_message,
    pick_store,
    preset_entry,
    usable_stores,
)
from untaped.sdk.network.auth import takes_token_command, token_env_names, token_override_env
from untaped.sdk.network.credentials import (
    SHORT_ID,
    Credential,
    CredentialKey,
    credential_key,
    describe_key,
    folder_chose,
    held_by_settings,
    legacy_setting,
    override_variable,
)
from untaped.sdk.plugin import Command, Group
from untaped.sdk.prompt.prompts import PromptChoice
from untaped.sdk.words import command_line, hint, plural, run_hint

_diag = Diagnostics()


# --------------------------------------------------------------------------- #
# Declared credentials: login, list, forget, status
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Target:
    """One declared credential and the key this run (or the command line) resolves it to."""

    credential: Credential[Any]
    key: CredentialKey | None
    """``None`` while the settings that hold it are unset."""
    problem: str | None = None
    """Why there is no key: the settings are unset or invalid."""

    @property
    def plugin(self) -> str:
        return self.credential.plugin

    def menu_label(self) -> str:
        """``awx token  url=https://… (stored 9b07e4)``."""
        where = describe_key(self.key) if self.key is not None else self.problem or "no key"
        answer = None if self.key is None else self.credential.answer(self.key)
        state = f" ({answer.source})" if answer is not None else ""
        return f"{self.credential.label}  {where}{state}"


def _declared(result: CompositionResult) -> list[Credential[Any]]:
    return [each for plugin in result.plugins for each in plugin.spec.credentials]


def _settings_model(result: CompositionResult, plugin: str) -> type[BaseModel] | None:
    spec = next((each.spec for each in result.plugins if each.spec.name == plugin), None)
    return None if spec is None or spec.settings is None else settings_model(spec)


def _run_target(result: CompositionResult, credential: Credential[Any]) -> _Target:
    """``credential`` with the key its section's settings give this run."""
    model = _settings_model(result, credential.plugin)
    if not held_by_settings(credential, model):
        return _Target(credential, None, problem="key given on the command line")
    try:
        settings = load_settings_section(credential.plugin)
    except ConfigError as exc:
        return _Target(credential, None, problem=str(exc))
    key = credential_key(credential, settings)
    return _Target(credential, key, problem=None if key is not None else "not set up")


def _choose(
    result: CompositionResult, plugin: str | None, name: str | None, *, verb: str
) -> Credential[Any]:
    """The credential ``plugin`` and ``name`` name, or one picked from a menu on a terminal.

    Off a terminal the plugin is required, and so is the name when the
    plugin declares several (exit 2, listing the choices), so nothing waits
    for input.
    """
    declared = _declared(result)
    if not declared:
        raise UsageError("no installed plugin declares a credential")
    choices = declared
    if plugin is not None:
        choices = [each for each in declared if each.plugin == plugin]
        if not choices:
            known = ", ".join(sorted({each.plugin for each in declared}))
            raise UsageError(f"{plugin} declares no credential; plugins that do: {known}")
        if name is not None:
            found = next((each for each in choices if each.name == name), None)
            if found is None:
                known = ", ".join(each.name for each in choices)
                raise UsageError(f"{plugin} has no credential {name!r}; it declares: {known}")
            return found
        if len(choices) == 1:
            return choices[0]
    ui = ui_context(strict=False)
    if not ui.can_prompt:
        listed = ", ".join(_spelled(each) for each in choices)
        what = "a plugin" if plugin is None else "a credential name"
        raise UsageError(
            f"name {what} when not at a terminal; choices: {listed}",
            hint=command_line(f"auth {verb} {_spelled(choices[0])}"),
        )
    targets = [_run_target(result, each) for each in choices]
    with ui.terminal():
        return ui.select(
            "Which credential?",
            [PromptChoice(each.credential, each.menu_label()) for each in targets],
        )


def _spelled(credential: Credential[Any]) -> str:
    """``awx`` for a plugin's ``token``, else ``<plugin> <name>``: what ``login`` takes."""
    if credential.name == "token" and credential.plugin:
        return credential.plugin
    return f"{credential.plugin} {credential.name}"


def _key(
    result: CompositionResult, credential: Credential[Any], fields: dict[str, str]
) -> CredentialKey:
    """The key to log in or forget: this run's from settings, or built from ``--key``s."""
    model = _settings_model(result, credential.plugin)
    if held_by_settings(credential, model):
        if fields:
            raise UsageError(
                f"{credential.label}'s key comes from {credential.plugin}'s settings, "
                "not from --key; pick another profile to use another server"
            )
        target = _run_target(result, credential)
        if target.key is None:
            raise ConfigError(
                f"{credential.plugin} is not set up, so there is no server to file "
                f"its {credential.name} for ({target.problem})",
                hint=command_line(f"setup plan --only {credential.plugin}"),
                fix_by="user",
            )
        return target.key
    try:
        key: CredentialKey = credential.key.model_validate(fields)
    except ValidationError as exc:
        names = " ".join(
            f"--key {name.replace('_', '-')}=…" for name in credential.key.model_fields
        )
        raise UsageError(
            f"{credential.label} needs {names}: {first_validation_error(exc)}"
        ) from exc
    return key


def _refuse_folder(credential: Credential[Any], key: CredentialKey, what: str) -> None:
    refused = credential.refusal(key, what)
    if refused is not None:
        refused.fix_by = "user"
        raise refused


def _login(
    result: CompositionResult,
    plugin: str | None,
    name: str | None,
    *,
    token_command: str | None,
    store: StoreName | None,
    fields: dict[str, str],
) -> CredentialOutcome:
    credential = _choose(result, plugin, name, verb="login")
    key = _key(result, credential, fields)
    _refuse_folder(credential, key, "logging in")
    _diag.info(f"{credential.label} for {describe_key(key)}")
    if token_command is not None:
        argv = _split_command(token_command)
        where = credential.store(key, command=argv)
    else:
        chosen = ready_store(credential, store)
        ui = ui_context(strict=False)
        token = _prompt_token(ui, credential.label) if ui.can_prompt else _read_stdin_token()
        where = store_token(credential, key, token, store=chosen)
    ident = credential.id(key)
    _diag.success(f"stored the {credential.label} for {describe_key(key)}: {where}")
    _name_outranked(credential)
    return CredentialOutcome(
        plugin=credential.plugin,
        name=credential.name,
        key=describe_key(key),
        id=ident[:SHORT_ID],
        store=where,
        action="stored",
    )


def _split_command(text: str) -> list[str]:
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise UsageError(f"--token-command is not a valid command line: {exc}") from exc
    if not argv:
        raise UsageError("--token-command is empty")
    return argv


def _name_outranked(credential: Credential[Any]) -> None:
    """Say which sources the stored login now beats, and which one still beats it."""
    variable = override_variable(credential.plugin, credential.name)
    if variable is not None:
        _diag.warning(f"${variable} is set and wins over this login until you unset it")
    legacy = legacy_setting(credential)
    if legacy is not None:
        setting = legacy[0]
        drop = (
            f"unset ${setting}"
            if setting.startswith("UNTAPED_")
            else f"{run_hint(f'config unset {setting}')} to drop the old setting"
        )
        _diag.info(f"this login now wins over {setting}; {drop}")
    found = next((each for each in credential.env if os.environ.get(each, "").strip()), None)
    if found is not None:
        _diag.info(f"this login now wins over ${found}")


def _forget(
    result: CompositionResult,
    plugin: str | None,
    name: str | None,
    *,
    ident: str | None,
    fields: dict[str, str],
    confirm: Confirm,
) -> CredentialOutcome:
    ui = ui_context(strict=False)
    if ident is not None:
        if plugin is not None or fields:
            raise UsageError("--id names the row on its own; leave out the plugin and --key")
        row = _row_by_id(ident)
        key_text = _row_key(row)
    else:
        if plugin is None and not ui.can_prompt:
            raise UsageError(
                "name a plugin or --id when not at a terminal",
                hint=command_line("auth list"),
            )
        credential = _choose(result, plugin, name, verb="forget")
        key = _key(result, credential, fields)
        key_text = describe_key(key)
        found = find_row(credential.id(key))
        if found is None:
            _diag.info(f"no stored {credential.label} for {key_text}")
            return CredentialOutcome(
                plugin=credential.plugin,
                name=credential.name,
                key=key_text,
                id=credential.id(key)[:SHORT_ID],
                store=None,
                action="unchanged",
            )
        row = found
    outcome = CredentialOutcome(
        plugin=row.plugin,
        name=row.name,
        key=key_text,
        id=row.short,
        store=row.where(),
        action="planned",
    )
    if not confirm(f"Forget the stored {row.plugin} {row.name} for {key_text} ({row.where()})?"):
        return outcome
    _forget_row(row)
    return outcome.model_copy(update={"action": "deleted"})


def _forget_row(row: CredentialRow) -> None:
    from untaped.sdk.network._credential_store import forget_row  # noqa: PLC0415

    forget_row(row.id)
    if row.migrated:
        _diag.success(
            f"forgot {row.plugin} {row.name} ({row.short}); left {row.where()}, "
            "which an older untaped still reads"
        )
    else:
        _diag.success(f"forgot {row.plugin} {row.name} ({row.short}) and deleted {row.where()}")
    _diag.info("nothing was revoked on the server")


def _row_by_id(ident: str) -> CredentialRow:
    found = rows_by_prefix(ident)
    if not found:
        raise UsageError(f"no stored credential has id {ident!r}", hint=command_line("auth list"))
    if len(found) > 1:
        listed = ", ".join(row.id[: SHORT_ID + 4] for row in found)
        raise UsageError(f"id {ident!r} matches several stored credentials: {listed}")
    return found[0]


def _row_key(row: CredentialRow) -> str:
    """The key as the row's label shows it (defaulted URLs too), else its stored fields."""
    prefix = f"{row.plugin} {row.name.replace('_', ' ')} "
    if row.label.startswith(prefix):
        return row.label.removeprefix(prefix)
    return " ".join(f"{name}={value}" for name, value in row.key.items()) or "(no key fields)"


def _list() -> list[StoredCredential]:
    listed = [
        StoredCredential(
            id=row.short,
            plugin=row.plugin,
            name=row.name,
            key=_row_key(row),
            backend=row.backend,
            where=row.where(),
        )
        for row in rows()
    ]
    if not listed:
        _diag.info("no stored credentials")
    return listed


def _status(result: CompositionResult) -> list[TokenSource]:
    profile = selected_profile()
    found: list[TokenSource] = []
    for credential in _declared(result):
        target = _run_target(result, credential)
        found.append(_status_row(target, profile))
    found.extend(_legacy_status(result))
    stores = ", ".join(store.name for store in usable_stores()) or "none"
    _diag.info(f"token stores usable here: {stores}")
    return found


def _status_row(target: _Target, profile: str) -> TokenSource:
    credential, key = target.credential, target.key
    row = TokenSource(
        profile=profile, section=credential.plugin, name=credential.name, source="none"
    )
    if key is None:
        return row.model_copy(update={"key": target.problem})
    update: dict[str, Any] = {"key": describe_key(key)}
    answer = credential.answer(key)
    if answer is not None:
        update |= {"source": answer.source, "step": answer.step}
        if answer.row is not None:
            update["id"] = answer.row[:SHORT_ID]
    if folder_chose(key):
        refused = credential.refusal(key, "a variable or old setting")
        update["refusal"] = str(refused) if refused is not None else None
    return row.model_copy(update=update)


# --------------------------------------------------------------------------- #
# 11.0's config token commands (auth set, unset, migrate; deprecated)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _TokenSection:
    """A composed section that takes ``token`` and ``token_command`` (11.0's settings)."""

    section: str
    env: tuple[str, ...]
    """Its conventional token environment variables, in order."""
    declared: bool
    """Whether its plugin declares a ``token`` credential (the settings are deprecated)."""


def _token_sections(result: CompositionResult) -> dict[str, _TokenSection]:
    """Every composed section whose settings take ``token`` and ``token_command``.

    A plugin that declares a ``token`` credential and still declares those
    settings itself (github's, until it moves) reads them, deprecated, so
    ``auth set`` keeps writing them.
    """
    found: dict[str, _TokenSection] = {}
    for registered in result.plugins:
        spec = registered.spec
        model = settings_model(spec)
        if "token" not in model.model_fields or not takes_token_command(model):
            continue
        credential = next((each for each in spec.credentials if each.name == "token"), None)
        env = credential.env if credential is not None else token_env_names(model.model_construct())
        found[spec.name] = _TokenSection(spec.name, env, declared=credential is not None)
    return found


def save_token(
    repo: SettingsFileRepository,
    section: str,
    profile: str,
    token: str,
    store: TokenStore,
) -> str:
    """Store ``token`` for ``section`` in ``profile`` and point the config at it.

    Validates the config write first, stores and reads the token back, then
    writes ``<section>.token_command`` and removes the profile's plaintext
    ``<section>.token``. Returns the entry's description. Nothing in the
    config changes when storing fails.
    """
    entry = entry_name(profile, section)
    refuse_inherited_token(section, profile)
    command = json.dumps(store.read_argv(entry))
    repo.set_value(f"{section}.token_command", command, profile=profile, dry_run=True)
    store.save(entry, token)
    repo.set_value(f"{section}.token_command", command, profile=profile)
    repo.unset_value(f"{section}.token", profile=profile)
    return store.describe(entry)


def inherited_from_default(
    section: str, profile: str, key: str, raw: dict[str, Any] | None = None
) -> bool:
    """Whether ``profile`` would inherit ``<section>.<key>`` from ``default``.

    ``raw`` is the config already read, if any.
    """
    if profile == DEFAULT_PROFILE:
        return False
    raw = read_config_dict() if raw is None else raw
    default = active_settings_layout().profile_data(raw, DEFAULT_PROFILE) or {}
    node = default.get(section)
    return isinstance(node, dict) and bool(node.get(key))


def refuse_inherited_token(section: str, profile: str) -> None:
    """Raise when ``default``'s plaintext token would win over ``profile``'s command."""
    if inherited_from_default(section, profile, "token"):
        raise ConfigError(
            f"{section}.token is set in profile {DEFAULT_PROFILE} and would override "
            f"{section}.token_command in profile {profile}; move it out of "
            f"{DEFAULT_PROFILE} first\n{hint('auth migrate')}"
        )


Deleted = Literal["deleted", "gone", "foreign"]
"""What :func:`delete_stored_token` found: it deleted the entry, the entry was
already gone, or the command is not one untaped wrote (nothing deleted)."""


def delete_stored_token(argv: Sequence[str]) -> tuple[Deleted, str | None]:
    """Delete the entry a preset ``token_command`` reads, and say where it was.

    An entry the store reports missing (:meth:`TokenStore.gone`) is not an
    error; an unreachable store is. A command untaped did not write is left
    alone (``"foreign"``, no location).
    """
    preset = preset_entry(argv)
    if preset is None:
        return "foreign", None
    store, entry = preset
    where = store.describe(entry)
    try:
        store.delete(entry)
    except ConfigError:
        if not store.gone(entry):
            raise
        return "gone", where
    return "deleted", where


def plaintext_token(data: dict[str, Any] | None, section: str) -> str | None:
    """The plaintext ``<section>.token`` in one profile's own ``data``, if any."""
    node = (data or {}).get(section)
    token = node.get("token") if isinstance(node, dict) else None
    return token.strip() if isinstance(token, str) and token.strip() else None


# --------------------------------------------------------------------------- #
# The command group
# --------------------------------------------------------------------------- #


def _composition() -> CompositionResult:
    # Lazy: bootstrap is the CLI's root and imports this module.
    from untaped.runtime.bootstrap import composition  # noqa: PLC0415

    return composition()


def _key_fields(given: list[str]) -> dict[str, str]:
    """``--key FIELD=VALUE`` pairs, by field name."""
    fields: dict[str, str] = {}
    for pair in given:
        name, sep, value = pair.partition("=")
        field = name.strip().replace("-", "_")
        if not sep or not field:
            raise UsageError(f"--key takes FIELD=VALUE, not {pair!r}")
        if field in fields:
            raise UsageError(f"--key names {name.strip()} more than once")
        fields[field] = value
    return fields


_PLUGIN = "The plugin, e.g. awx; a menu on a terminal when left out."
_NAME = "The credential, when the plugin declares several."
_KEY = "A key field the plugin's settings don't hold, as FIELD=VALUE; repeat for several."
_STORE = "Store to use; default: the first usable one."


class Login(Command[CoreSettings]):
    """Store a token for this run's server, in this machine's password store.

    The server is the plugin's settings for the profile this run picks
    (awx.server); the key is printed before anything is asked. The token is
    read from a hidden prompt, or from stdin when stdin is not a terminal;
    --token-command stores a command instead (its program a bare name or an
    absolute path, run from the config file's folder). The store is macOS
    security, secret-tool (Secret Service), then pass, or --store. A login
    wins over the deprecated PLUGIN.token settings and the plugin's
    variables; UNTAPED_PLUGIN__TOKEN still wins over it. Logging in again
    replaces it. A server a folder's settings chose is refused.

    Examples:
        $ untaped --profile prod auth login awx
        $ untaped auth login github --token-command 'gh auth token'
    """

    plugin: Positional[str | None] = Field(None, description=_PLUGIN)
    name: Positional[str | None] = Field(None, description=_NAME)
    token_command: str | None = Field(
        None, description="Store a command that prints the token, run each time it is needed."
    )
    store: StoreName | None = Field(None, description=_STORE)
    key: list[str] = Field(default_factory=list, description=_KEY)
    output: Output[CredentialOutcome]

    def run(self) -> CredentialOutcome:
        return _login(
            _composition(),
            self.plugin,
            self.name,
            token_command=self.token_command,
            store=self.store,
            fields=_key_fields(self.key),
        )


class List(Command[CoreSettings]):
    """Show every stored credential, whichever profile names it; never a secret.

    A command is shown by its program only. auth status shows what this run
    uses.

    Examples:
        $ untaped auth list
    """

    output: Output[StoredCredential]

    def run(self) -> Iterator[StoredCredential]:
        yield from _list()


class Forget(Command[CoreSettings]):
    """Delete the stored credential for this run's server, or the one --id names.

    Takes auth login's arguments; --id reaches a row no profile names any
    more. Nothing is revoked on the server: revoke the token there too. A
    credential moved from 11.0's settings leaves its old entry, which an
    older untaped still reads.

    Examples:
        $ untaped --profile prod auth forget awx --yes
        $ untaped auth forget --id 9b07e4 --dry-run
    """

    plugin: Positional[str | None] = Field(None, description=_PLUGIN)
    name: Positional[str | None] = Field(None, description=_NAME)
    id: str | None = Field(
        None, description="The stored row's id from `auth list` (a prefix will do)."
    )
    key: list[str] = Field(default_factory=list, description=_KEY)
    confirm: Confirm
    output: Output[CredentialOutcome]

    def run(self) -> CredentialOutcome:
        return _forget(
            _composition(),
            self.plugin,
            self.name,
            ident=self.id,
            fields=_key_fields(self.key),
            confirm=self.confirm,
        )


class Status(Command[CoreSettings]):
    """Show which source answers each credential for this run, and which stores work here.

    Reads and runs nothing, so it cannot tell whether a stored entry still
    exists; untaped doctor --online checks that. A plugin still on 11.0's
    token settings shows one row per profile.

    Examples:
        $ untaped auth status
        $ untaped --profile prod auth status -f json
    """

    output: Output[TokenSource]

    def run(self) -> Iterator[TokenSource]:
        yield from _status(_composition())


_REMOVED = 12


class Set(Command[CoreSettings]):
    """Store a token with this machine's password store and point the config at it.

    Tries macOS security, secret-tool (Secret Service), then pass. The token
    is read from a hidden prompt (or --stdin), stored on the tool's stdin,
    read back, and only then written as SECTION.token_command in the
    profile --profile names, else the base, replacing any plaintext
    SECTION.token. Running it again replaces the stored token.

    Examples:
        $ printf %s "$TOKEN" | untaped auth set github --stdin
    """

    stability = deprecated(removed_in=_REMOVED, replacement="untaped auth login")

    section: Positional[str] = Field(description="Section whose token to store, e.g. awx.")
    stdin: bool = Field(False, description="Read the token from stdin.")
    store: StoreName | None = Field(None, description=_STORE)
    output: Output[AuthOutcome]

    def run(self) -> AuthOutcome:
        return _set(_composition(), self.section, stdin=self.stdin, store=self.store)


class Unset(Command[CoreSettings]):
    """Delete the token auth set stored and unset SECTION.token_command.

    Acts on the picked profile's own setting only (or the base); an
    inherited one is left alone. A token_command untaped did not write is
    left alone too.

    Examples:
        $ untaped auth unset github --dry-run
    """

    stability = deprecated(removed_in=_REMOVED, replacement="untaped auth forget")

    section: Positional[str] = Field(description="Section whose stored token to remove.")
    confirm: Confirm
    output: Output[AuthOutcome]

    def run(self) -> AuthOutcome:
        return _unset(_composition(), self.section, confirm=self.confirm)


class Migrate(Command[CoreSettings]):
    """Move every plaintext token in config.yml, in every profile, to a store.

    Each token is stored and read back before it is removed from the file;
    one that fails stays where it was, and the command exits non-zero, with
    the code of the most severe failure's category.

    Examples:
        $ untaped auth migrate --dry-run
    """

    stability = deprecated(removed_in=_REMOVED, replacement="untaped auth login")

    store: StoreName | None = Field(None, description=_STORE)
    dry_run: bool = Field(False, description="Check the moves and write nothing.")
    output: Output[AuthOutcome]

    def run(self) -> Iterator[AuthOutcome]:
        yield from _migrate(_composition(), store=self.store, dry_run=self.dry_run)


class Auth(Group):
    """Log in to the servers plugins talk to; keep tokens safe."""

    commands = (Login, List, Forget, Status, Set, Unset, Migrate)


def _section(result: CompositionResult, section: str) -> _TokenSection:
    sections = _token_sections(result)
    if section not in sections:
        known = ", ".join(sorted(sections)) or "(none)"
        raise ConfigError(f"unknown token section {section!r}; known: {known}", category="invalid")
    return sections[section]


def _target_profile() -> str:
    return write_target()


def _set(
    result: CompositionResult,
    section: str,
    *,
    stdin: bool,
    store: StoreName | None,
) -> AuthOutcome:
    spec = _section(result, section)
    profile = _target_profile()
    entry_name(profile, section)
    refuse_inherited_token(section, profile)
    chosen = pick_store(store)
    if chosen is None:
        raise ConfigError(no_store_message(section, spec.env), category="unavailable")
    chosen.preflight()
    ui = ui_context(strict=False)
    token = _legacy_stdin_token() if stdin else _legacy_prompt(ui, f"{section} token")
    where = save_token(SettingsFileRepository(), section, profile, token, chosen)
    _diag.success(
        f"stored the {section} token in {where}; {section}.token_command set in profile {profile}"
    )
    _warn_env_override(section)
    return AuthOutcome(section=section, profile=profile, store=where, action="stored")


def _read_stdin_token() -> str:
    token = read_stdin_text().strip()
    if not token:
        raise UsageError("no token received on stdin")
    if "\n" in token or "\r" in token:
        raise UsageError("stdin should hold the token on one line")
    return token


def _legacy_stdin_token() -> str:
    token = read_stdin_text().strip()
    if not token:
        raise ConfigError("no token received on stdin", category="invalid")
    if "\n" in token or "\r" in token:
        raise ConfigError("--stdin expects the token on one line", category="invalid")
    return token


def _legacy_prompt(ui: UiContext, label: str) -> str:
    with ui.terminal(refusal="no terminal to prompt for the token; pipe it with --stdin"):
        return ui.secret(label).strip()


def _prompt_token(ui: UiContext, label: str) -> str:
    with ui.terminal(refusal="no terminal to prompt for the token; pipe it on stdin"):
        return ui.secret(label).strip()


def _warn_env_override(section: str) -> None:
    override = token_override_env(section)
    if override is not None:
        _diag.warning(
            f"${override} is set and wins over the stored token until you unset it",
        )


def _unset(
    result: CompositionResult,
    section: str,
    *,
    confirm: Confirm,
) -> AuthOutcome:
    _section(result, section)
    profile = _target_profile()
    raw = read_config_dict()
    own = (active_settings_layout().profile_data(raw, profile) or {}).get(section)
    argv = own.get("token_command") if isinstance(own, dict) else None
    row = AuthOutcome(section=section, profile=profile, store=None, action="unchanged")
    if not argv:
        _, provenance = resolve_profiles(raw, picks=(profile,))
        holder = provenance.get((section, "token_command"))
        if holder is not None and holder != profile:
            _diag.info(
                f"{section}.token_command is inherited from profile {holder}; "
                f"profile {profile} is unchanged"
            )
            _diag.hint(run_hint(f"--profile {holder} auth unset {section}"))
        else:
            _diag.info(f"{section}.token_command is not set in profile {profile}")
        return row
    preset = preset_entry(argv)
    if preset is None:
        raise ConfigError(
            f"{section}.token_command in profile {profile} was not written by untaped; "
            f"remove it yourself\n{hint(f'config unset {section}.token_command')}",
            category="invalid",
        )
    where = preset[0].describe(preset[1])
    row = row.model_copy(update={"store": where})
    question = (
        f"Delete the {section} token from {where} and unset {section}.token_command "
        f"in profile {profile}?"
    )
    if not confirm(question):
        return row.model_copy(update={"action": "planned"})
    repo = SettingsFileRepository()
    # A dry run first: a write the config refuses must not cost the stored token.
    repo.unset_value(f"{section}.token_command", profile=profile, dry_run=True)
    deleted, _ = delete_stored_token(argv)
    repo.unset_value(f"{section}.token_command", profile=profile)
    if deleted == "gone":
        _diag.success(
            f"the {section} token was already gone from {where}; "
            f"unset {section}.token_command (profile {profile})"
        )
    else:
        _diag.success(f"deleted the {section} token from {where} (profile {profile})")
    return row.model_copy(update={"action": deleted})


def _legacy_status(result: CompositionResult) -> list[TokenSource]:
    """11.0's ``auth status`` rows, one per profile, for plugins that declare no credential."""
    raw = read_config_dict()
    layout = active_settings_layout()
    profiles = layout.profile_names(raw)
    sections = [each for each in _token_sections(result).values() if not each.declared]
    found: list[TokenSource] = []
    for profile in profiles:
        effective, provenance = resolve_profiles(raw, picks=(profile,))
        for spec in sections:
            node = effective.get(spec.section)
            source, key = _source(spec, node if isinstance(node, dict) else {})
            # Other profiles inherit `default`; list them only for a token of their own
            # (one that default's plaintext token shadows drops out too).
            if profile != DEFAULT_PROFILE and (
                key is None or provenance.get((spec.section, key)) != profile
            ):
                continue
            found.append(TokenSource(profile=profile, section=spec.section, source=source))
    return found


def _source(spec: _TokenSection, node: dict[str, Any]) -> tuple[str, str | None]:
    """Name a section's token source and the config key that sets it, if any."""
    override = token_override_env(spec.section)
    if override is not None:
        return f"${override}", None
    token = node.get("token")
    if isinstance(token, str) and token.strip():
        return "config.yml (plain text)", "token"
    argv = node.get("token_command")
    if isinstance(argv, list) and argv:
        preset = preset_entry([str(part) for part in argv])
        return (preset[0].describe(preset[1]) if preset else "token_command"), "token_command"
    env = next((name for name in spec.env if os.environ.get(name, "").strip()), None)
    return (f"${env}" if env else "none"), None


def _migrate(
    result: CompositionResult,
    *,
    store: StoreName | None,
    dry_run: bool,
) -> Iterator[AuthOutcome]:
    raw = read_config_dict()
    layout = active_settings_layout()
    # `default` first: its token would otherwise win over another profile's command.
    ordered = layout.profile_names(raw)
    sections = _token_sections(result)
    pending = [
        (profile, section, token)
        for profile in ordered
        for section in sections
        if (token := plaintext_token(layout.profile_data(raw, profile), section)) is not None
    ]
    if not pending:
        _diag.info("no plaintext tokens in the config; nothing to move")
        return
    chosen = pick_store(store)
    if chosen is None:
        raise ConfigError(
            no_store_message(pending[0][1], sections[pending[0][1]].env),
            category="unavailable",
        )
    if not dry_run:
        chosen.preflight()
    moved, failures = _move_all(pending, chosen, dry_run=dry_run)
    yield from moved
    if failures:
        worst = most_severe(failures)
        raise ConfigError(
            f"{plural(len(failures), 'token')} could not be moved and stay in the config",
            category=worst.category,
            system=worst.system,
            hint=next((exc.hint for exc in failures if exc.hint), None),
        )
    if not dry_run:
        _diag.success(f"moved {plural(len(moved), 'token')} to {chosen.name}")


def _move_all(
    pending: Sequence[tuple[str, str, str]], store: TokenStore, *, dry_run: bool
) -> tuple[list[AuthOutcome], list[ConfigError]]:
    """Move each token; also the failures, each already a ``failed`` row."""
    repo = SettingsFileRepository()
    moved: list[AuthOutcome] = []
    failures: list[ConfigError] = []
    for profile, section, token in pending:
        row = partial(AuthOutcome, section=section, profile=profile)
        try:
            entry = entry_name(profile, section)
            if dry_run:
                moved.append(row(store=store.describe(entry), action="planned"))
                continue
            where = save_token(repo, section, profile, token, store)
        except ConfigError as exc:
            failures.append(exc)
            error = Failure.from_exception(exc)
            moved.append(row(store=None, action="failed", detail=str(exc), error=error))
            continue
        moved.append(row(store=where, action="moved"))
    return moved, failures


__all__ = [
    "Auth",
    "Deleted",
    "delete_stored_token",
    "inherited_from_default",
    "plaintext_token",
    "refuse_inherited_token",
    "save_token",
]
