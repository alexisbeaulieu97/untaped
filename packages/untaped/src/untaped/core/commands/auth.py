"""Root ``untaped auth …`` command group: the credentials plugins declare.

``auth login`` files a secret for a declared
:class:`~untaped.sdk.network.credentials.Credential` and the key it is for (this run's
server), in this machine's password store or as a command that prints it;
``auth list`` shows every stored one; ``auth forget`` deletes one;
``auth status`` says, for this run, which source answers each credential,
without reading anything. Secrets are read from a hidden prompt or stdin,
never from an argument.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from untaped.core.commands.records import (
    CredentialOutcome,
    StoredCredential,
    TokenSource,
)
from untaped.core.settings import CoreSettings
from untaped.runtime.profile_resolver import (
    selected_profile,
)
from untaped.runtime.registry import CompositionResult, settings_model
from untaped.runtime.settings import load_settings_section
from untaped.runtime.sink import Diagnostics
from untaped.sdk._stdin import read_stdin_text
from untaped.sdk._ui import Ui, build_terminal
from untaped.sdk.errors import ConfigError, UsageError, first_validation_error
from untaped.sdk.fields import Confirm, Output, Positional
from untaped.sdk.network._credential_store import (
    INDEX,
    CredentialRow,
    find_row,
    ids_by_prefix,
    ready_store,
    rows,
    store_token,
)
from untaped.sdk.network._token_store import (
    StoreName,
    usable_stores,
)
from untaped.sdk.network.credentials import (
    SHORT_ID,
    Credential,
    CredentialKey,
    credential_key,
    describe_key,
    folder_chose,
    held_by_settings,
    override_variable,
)
from untaped.sdk.plugin import Command, Group
from untaped.sdk.prompt.prompts import PromptChoice
from untaped.sdk.words import command_line

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
    ui = build_terminal(strict=False)
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
        ui = build_terminal(strict=False)
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
    ui = build_terminal(strict=False)
    if ident is not None:
        if plugin is not None or fields:
            raise UsageError("--id names the row on its own; leave out the plugin and --key")
        ident = _id_by_prefix(ident)
        try:
            row = INDEX.get(ident)
        except ConfigError:  # a damaged row: forget the row, its entry is unknown
            return _forget_damaged(ident, confirm)
        assert row is not None  # its id was just listed
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
    _diag.success(f"forgot {row.plugin} {row.name} ({row.short}) and deleted {row.where()}")
    _diag.info("nothing was revoked on the server")


def _id_by_prefix(prefix: str) -> str:
    found = ids_by_prefix(prefix)
    if not found:
        raise UsageError(f"no stored credential has id {prefix!r}", hint=command_line("auth list"))
    if len(found) > 1:
        listed = ", ".join(ident[: SHORT_ID + 4] for ident in found)
        raise UsageError(f"id {prefix!r} matches several stored credentials: {listed}")
    return found[0]


def _forget_damaged(ident: str, confirm: Confirm) -> CredentialOutcome:
    """Forget the damaged row ``ident``: the row only, as what it points at can't be read."""
    outcome = CredentialOutcome(
        plugin="unknown",
        name="unknown",
        key="(a damaged row)",
        id=ident[:SHORT_ID],
        store=None,
        action="planned",
    )
    if not confirm(f"Forget the damaged stored credential {ident[:SHORT_ID]}?"):
        return outcome
    INDEX.delete(ident)
    _diag.success(f"forgot the damaged stored credential {ident[:SHORT_ID]}")
    _diag.info("its store entry, if it had one, was not deleted; nothing was revoked on the server")
    return outcome.model_copy(update={"action": "deleted"})


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
        refused = credential.refusal(key, "a variable")
        update["refusal"] = str(refused) if refused is not None else None
    return row.model_copy(update=update)


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
    wins over the plugin's variables; UNTAPED_PLUGIN__TOKEN still wins over
    it. Logging in again
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
    more. Nothing is revoked on the server: revoke the token there too.

    Examples:
        $ untaped --profile prod auth forget awx --yes
        $ untaped auth forget --id 9b07e4 --dry-run
    """

    removes = INDEX

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
    exists; untaped doctor --online checks that.

    Examples:
        $ untaped auth status
        $ untaped --profile prod auth status -f json
    """

    output: Output[TokenSource]

    def run(self) -> Iterator[TokenSource]:
        yield from _status(_composition())


class Auth(Group):
    """Log in to the servers plugins talk to; keep tokens safe."""

    commands = (Login, List, Forget, Status)


def _read_stdin_token() -> str:
    token = read_stdin_text().strip()
    if not token:
        raise UsageError("no token received on stdin")
    if "\n" in token or "\r" in token:
        raise UsageError("stdin should hold the token on one line")
    return token


def _prompt_token(ui: Ui, label: str) -> str:
    with ui.terminal(refusal="no terminal to prompt for the token; pipe it on stdin"):
        return ui.secret(label).strip()


__all__ = ["Auth"]
