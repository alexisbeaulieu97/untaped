"""``config migrate``'s credential step: 11.0 config tokens become stored logins.

For each profile (``default`` first) and each declared credential, the key
is resolved from the config file alone (``default`` under the profile,
renames applied; root ``http.verify_ssl: false`` read as the key's
``verify_tls: false``, as every run reads it, and written there). Each
source the profile reads becomes its own row:

- a ``token_command`` untaped wrote (``auth set``) points at its entry,
  which stays where it is: an older untaped still reads it;
- one inherited from ``default`` for another server is copied to a new
  entry;
- any other command keeps its argv, which runs from the config folder;
- a plain-text token goes to this machine's password store.

A leftover stays in the file and keeps warning on every run that reads it:
two profiles filing different sources for one server, a plain-text token
with no store, or a key the file doesn't set (only the environment does).
The migrated lines leave the file. :func:`plan` reads only; :func:`apply`
stores, then edits the file once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from untaped.auth import read_command
from untaped.config.repository import SettingsFileRepository
from untaped.credential_store import (
    file_reference,
    find_row,
    store_command,
    store_token,
)
from untaped.credentials import (
    Credential,
    CredentialKey,
    credential_key,
    declared_credentials,
    describe_key,
    key_fields,
    key_shape,
)
from untaped.errors import ConfigError
from untaped.profile_resolver import DEFAULT_PROFILE
from untaped.settings import active_settings_layout, validate_settings_section
from untaped.token_store import TokenStore, preset_entry

__all__ = ["CredentialMove", "apply", "plan"]

type How = Literal["reference", "copy", "command", "store", "same", "outranked", "leftover"]


@dataclass(frozen=True)
class CredentialMove:
    """One old credential line one profile reads, and what ``config migrate`` does with it."""

    profile: str
    setting: str
    """The line: ``awx.token`` or ``awx.token_command``."""
    how: How
    detail: str
    credential: Credential[Any] | None = None
    key: CredentialKey | None = None
    argv: tuple[str, ...] | None = None
    token: str | None = field(default=None, repr=False)
    preset: tuple[TokenStore, str] | None = None
    owner: str | None = None
    """The profile whose file holds the line (``default`` for an inherited one)."""
    tls: tuple[str, ...] | None = None
    """Where this profile gets ``verify_tls: false`` from root ``verify_ssl``."""
    clash: str | None = None
    """The profile whose different line for the same server holds this one back."""

    @property
    def moves(self) -> bool:
        """Whether its line leaves the file."""
        return self.how != "leftover" and self.owner == self.profile


def plan(data: Mapping[str, Any], *, store_available: bool) -> list[CredentialMove]:
    """What migrating ``data``'s credential lines would do; reads and stores nothing.

    ``store_available``: whether a password store (or the test store) can
    take a plain-text token.
    """
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        return []
    names = sorted(profiles, key=lambda name: (name != DEFAULT_PROFILE, name))
    by_plugin: dict[str, list[Credential[Any]]] = {}
    for credential in declared_credentials():
        by_plugin.setdefault(credential.plugin, []).append(credential)
    layout = active_settings_layout()
    moves: list[CredentialMove] = []
    filed: dict[str, CredentialMove] = {}
    for profile in names:
        resolved = layout.resolve(dict(data), profile=profile)
        effective = resolved.effective
        entry = profiles.get(profile)
        own: Mapping[str, Any] = entry if isinstance(entry, dict) else {}
        for section, credentials in sorted(by_plugin.items()):
            values = effective.get(section)
            if not isinstance(values, dict):
                continue
            held = own.get(section)
            mine: Mapping[str, Any] = held if isinstance(held, dict) else {}
            for credential in credentials:
                move = _plan_one(
                    credential,
                    profile=profile,
                    values=values,
                    mine=mine,
                    effective=effective,
                    tls_off=resolved.tls_off,
                    filed=filed,
                    store_available=store_available,
                )
                if move is not None:
                    moves.append(move)
    return _keep_read_lines(_hold_clashes(moves))


def _plan_one(
    credential: Credential[Any],
    *,
    profile: str,
    values: Mapping[str, Any],
    mine: Mapping[str, Any],
    effective: Mapping[str, Any],
    tls_off: frozenset[tuple[str, ...]],
    filed: dict[str, CredentialMove],
    store_available: bool,
) -> CredentialMove | None:
    section, name = credential.plugin, credential.name
    command_field = f"{name}_command"
    raw_token, raw_argv = values.get(name), values.get(command_field)
    token = raw_token.strip() if isinstance(raw_token, str) and raw_token.strip() else None
    argv = [str(each) for each in raw_argv] if isinstance(raw_argv, list) and raw_argv else None
    if token is not None:
        line, argv = name, None
    elif argv is not None:
        line = command_field
    else:
        return None
    owned = line in mine
    setting = f"{section}.{line}"
    owner = profile if owned or profile == DEFAULT_PROFILE else DEFAULT_PROFILE
    base = CredentialMove(profile, setting, "leftover", "", credential=credential, owner=owner)
    key, tls = _key(credential, effective, tls_off)
    if key is None:
        return replace(
            base,
            detail=f"profile {profile}'s file sets no {section} server; set one there and run "
            "`untaped config migrate` again",
        )
    base = replace(base, key=key, tls=tls)
    ident = credential.id(key)
    earlier = filed.get(ident)
    if earlier is not None:
        if earlier.owner == owner and earlier.setting == setting:
            return None  # the same line, read by an inheriting profile for the same server
        if (earlier.argv, earlier.token) == (None if argv is None else tuple(argv), token):
            return replace(base, how="same", detail=f"profile {earlier.profile} files the same")
        return replace(base, detail=_clash(earlier.profile, credential, key), clash=earlier.profile)
    if find_row(ident) is not None:
        move = replace(base, how="outranked", detail="a stored login already answers")
        filed[ident] = move
        return move
    inherited = owner != profile
    if argv is None:
        move = (
            replace(base, how="store", token=token, detail="stored in the password store")
            if store_available
            else replace(base, detail="no password store on this machine; it stays")
        )
    elif (found := preset_entry(argv)) is not None:
        store, entry = found
        how: How = "copy" if inherited else "reference"
        detail = (
            f"copied from {store.describe(entry)}"
            if inherited
            else f"reads {store.describe(entry)}, as before"
        )
        move = replace(base, how=how, argv=tuple(argv), preset=found, detail=detail)
    else:
        relative = _relative(argv)
        detail = "the command is kept"
        if relative:
            detail += f"; {relative} is relative, so it runs from the config folder"
        move = replace(base, how="command", argv=tuple(argv), detail=detail)
    if move.how != "leftover":
        filed[ident] = move
    return move


def _key(
    credential: Credential[Any],
    effective: Mapping[str, Any],
    tls_off: frozenset[tuple[str, ...]],
) -> tuple[CredentialKey | None, tuple[str, ...] | None]:
    """The key from the file's values, and its TLS leaf when root ``verify_ssl`` turned it off.

    The key is the one this run uses (root ``verify_ssl: false`` already
    read as its ``verify_tls: false``), so a login filed for it is found.
    """
    section = credential.plugin
    try:
        settings: BaseModel = validate_settings_section(dict(effective), section)
    except ValidationError:
        return None, None
    key = credential_key(credential, settings)
    if key is None:
        return None, None
    tls = key_shape(type(key)).tls
    path = next((path for path, each in key_fields(type(settings)) if each is credential.key), None)
    if tls is None or path is None or (section, *path, tls) not in tls_off:
        return key, None
    return key, (section, *path, tls)


def _relative(argv: Sequence[object]) -> str | None:
    for part in (str(each) for each in argv):
        if ("/" in part or part.startswith(".")) and not part.startswith(("/", "~", "-")):
            return part
    return None


def _clash(other: str, credential: Credential[Any], key: CredentialKey) -> str:
    return (
        f"profile {other} files another {credential.label} for {describe_key(key)}; "
        "keep one and run `untaped config migrate` again"
    )


def _hold_clashes(moves: list[CredentialMove]) -> list[CredentialMove]:
    """Keep every line for a server two profiles file differently for.

    A stored login would answer for each of them, so none is filed, and a
    line that only matched another (``same``) stays too.
    """
    clashes: dict[str, set[str]] = {}
    for move in moves:
        ident = _ident(move)
        if move.clash is not None and ident is not None:
            clashes.setdefault(ident, set()).update({move.clash, move.profile})
    found: list[CredentialMove] = []
    for move in moves:
        ident, credential, key = _ident(move), move.credential, move.key
        held = clashes.get(ident or "")
        if held and credential is not None and key is not None and move.clash is None:
            other = sorted(held - {move.profile})[0]
            move = replace(move, how="leftover", detail=_clash(other, credential, key))
        found.append(move)
    return found


def _ident(move: CredentialMove) -> str | None:
    if move.credential is None or move.key is None:
        return None
    return move.credential.id(move.key)


def _keep_read_lines(moves: list[CredentialMove]) -> list[CredentialMove]:
    """Keep a ``default`` line that a profile left over still reads."""
    still_read = {
        (move.owner, move.setting)
        for move in moves
        if move.how == "leftover" and move.owner != move.profile
    }
    return [
        replace(move, how="leftover", detail=f"{move.detail}; another profile still reads it")
        if move.moves and (move.owner, move.setting) in still_read
        else move
        for move in moves
    ]


def apply(moves: Sequence[CredentialMove]) -> list[CredentialMove]:
    """Store each move's credential, then take the migrated lines out of the file.

    A store that fails turns its move into a leftover (its line stays).
    Returns the moves as done.
    """
    done = [_store(move) for move in moves]
    removed = [move for move in done if move.moves]
    tls = [move for move in done if move.tls is not None and move.how != "leftover"]
    unset = [
        (move.profile, (move.credential.plugin, line))
        for move in removed
        if move.credential is not None
        # the unused one of the two goes too
        for line in (move.credential.name, f"{move.credential.name}_command")
    ]
    put = [(move.profile, move.tls, False) for move in tls if move.tls is not None]
    if unset or put:
        SettingsFileRepository().edit_profiles(unset=unset, put=put)
    return done


def _store(move: CredentialMove) -> CredentialMove:
    credential, key = move.credential, move.key
    if credential is None or key is None:
        return move
    try:
        refused = credential.refusal(key, "filing a credential")
        if refused is not None:
            raise refused
        match move.how:
            case "reference" if move.preset is not None:
                file_reference(credential, key, *move.preset)
            case "copy" if move.preset is not None:
                store, entry = move.preset
                secret = read_command(store.read_argv(entry), setting=move.setting)
                store_token(credential, key, secret)
            case "command" if move.argv is not None:
                store_command(credential, key, move.argv)
            case "store" if move.token is not None:
                store_token(credential, key, move.token)
    except ConfigError as exc:
        return replace(move, how="leftover", detail=f"{exc}; it stays")
    return move


def rows(moves: Sequence[CredentialMove], *, planned: bool) -> list[dict[str, str]]:
    """``config migrate``'s outcome rows for ``moves``."""
    found: list[dict[str, str]] = []
    for move in moves:
        action = "planned" if planned and move.how != "leftover" else _ACTIONS[move.how]
        to = "" if move.how == "leftover" else _to(move)
        found.append(
            {
                "profile": move.profile,
                "from": move.setting,
                "to": to,
                "action": action,
                "detail": move.detail,
            }
        )
        if move.tls is not None and move.how != "leftover":
            found.append(
                {
                    "profile": move.profile,
                    "from": "http.verify_ssl",
                    "to": ".".join(move.tls),
                    "action": "planned" if planned else "copied",
                    "detail": "root http.verify_ssl stays for an older untaped",
                }
            )
    return found


_ACTIONS: Mapping[str, str] = {
    "reference": "stored",
    "copy": "stored",
    "command": "stored",
    "store": "stored",
    "same": "dropped",
    "outranked": "dropped",
    "leftover": "kept",
}


def _to(move: CredentialMove) -> str:
    credential, key = move.credential, move.key
    if credential is None or key is None:
        return ""
    return f"login {credential.plugin} {credential.name} ({describe_key(key)})"
