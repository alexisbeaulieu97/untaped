"""What ``untaped setup`` writes: a plugin's setup form, or one setting from its row.

:func:`write_setting` writes one setting through the use case ``config set``
runs (creating the profile first when it is new). :func:`write_candidate`
writes one plugin's form once its check passed (or the user said so): create
the profile once, write the settings the user changed in one change
(:class:`~untaped.config.use_cases.SetSettings`), then each secret's branch
(``store``, ``move``, ``enter``, ``command``, ``env`` or ``keep``), retiring
the entry a replaced preset command read. Both run as the screen's write
commands, so they print nothing: their lines come back as notes for the
screen to print after it closes.

:class:`Candidate` is what the user asked for, in one place: the check reads it
as overlay values (:func:`overlay_values`) and the write reads it as branches,
so the two cannot disagree about what "this secret's source" means. A typed
secret is a ``SecretStr`` throughout and is unwrapped only where it is stored.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Collection
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import BaseModel, SecretStr

from untaped.auth import token_env_names
from untaped.config.models import SettingOutcome
from untaped.config.repository import SettingsFileRepository
from untaped.config.use_cases import (
    Note,
    RankProviders,
    SetSetting,
    SetSettings,
    SettingChange,
    UnsetSetting,
)
from untaped.credentials import Credential, credential_key, describe_key, key_fields
from untaped.errors import ConfigError, UsageError
from untaped.management.auth import delete_stored_token, save_token
from untaped.management.setup_state import ServiceState, profile_exists, service_marks
from untaped.plugins.registry import PluginSpec, settings_model
from untaped.profile.repository import ProfileFileRepository
from untaped.profile.use_cases import CreateProfile
from untaped.profile_resolver import DEFAULT_PROFILE, profile_scope
from untaped.settings import env_var_setting, load_settings_section
from untaped.token_store import TokenStore, entry_name, preset_entry

__all__ = [
    "Candidate",
    "RankTarget",
    "SecretChoice",
    "ensure_profile",
    "overlay_values",
    "write_candidate",
    "write_ranking",
    "write_setting",
]

type Checked = Literal["passed", "unchecked", "failed"]
"""What the check said about the candidate: it passed, there was none to run, or the user
saved it anyway after it failed."""


@dataclass(frozen=True)
class SecretChoice:
    """How the user chose to supply one secret: its tab, and what the tab holds.

    ``name`` is the credential's (or, for the legacy fallback, its
    ``token`` setting's); ``how`` is the tab (``keep``, ``move``,
    ``store``, ``enter``, ``command``, ``env``); ``token`` is the typed
    secret of ``store`` and ``enter``, ``argv`` the command of ``command``.
    """

    name: str
    how: str
    token: SecretStr | None = field(default=None, repr=False)
    argv: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Candidate:
    """One plugin's setup form as the user filled it in, not yet written.

    ``values`` are the settings the user changed, as ``(key, text)`` in
    form order, each as ``config set`` takes it (``None`` unsets it);
    ``expected`` is each one's own value in the profile when the form
    opened. Neither is in a repr: a masked setting is among them.
    ``secrets`` is one choice per secret, in form order.
    """

    name: str
    section: str
    profile: str
    values: tuple[tuple[str, str | None], ...] = field(default=(), repr=False)
    secrets: tuple[SecretChoice, ...] = ()
    expected: tuple[tuple[str, object], ...] = field(default=(), repr=False)
    checked: Checked = "unchecked"

    @property
    def commands(self) -> tuple[tuple[str, ...], ...]:
        """The argv of each secret the user chose to read from a command."""
        return tuple(each.argv for each in self.secrets if each.argv is not None)


@dataclass(frozen=True)
class RankTarget:
    """A contract method's ranking: its owner, contract and method, and its providers.

    The row adding one has no contract yet (``contract`` and ``method`` empty).
    """

    owner: str
    contract: str = ""
    method: str = ""
    providers: tuple[str, ...] = ()


def overlay_values(candidate: Candidate, current: ServiceState) -> dict[str, object]:
    """The settings overlay that stands in for ``candidate`` while it is checked.

    Each changed setting as the value a write would store; then each secret's
    choice. A ``None`` unsets the key: a command source removes the
    profile's token, the environment removes both the token and the
    profile's own command (they would win over the variable); ``keep``
    changes nothing. A declared credential reads its candidate from its
    name and ``<name>_command`` (:meth:`~untaped.credentials.Credential.get`).
    """
    repo = SettingsFileRepository()
    section = candidate.section
    values: dict[str, object] = {
        key: None if text is None else repo.coerced(f"{section}.{key}", text)
        for key, text in candidate.values
    }
    for secret in candidate.secrets:
        name, command = secret.name, f"{secret.name}_command"
        match secret.how:
            case "store" | "enter":
                values[name] = secret.token
            case "move":
                values[name] = None if current.plaintext is None else SecretStr(current.plaintext)
            case "command":
                values[command] = list(secret.argv or ())
                values[name] = None
            case "env":
                values[name] = None
                # The profile's own command reads the token; it would win over the variable.
                if current.own_command is not None and name == "token":
                    values[command] = None
    return values


def write_candidate(
    spec: PluginSpec,
    candidate: Candidate,
    current: ServiceState,
    store: TokenStore | None,
    preflight: Callable[[], None],
) -> list[Note]:
    """Write ``candidate``; return the lines to print about it.

    The store is tested first (``preflight``, once per run), so a store that
    cannot work leaves nothing behind, not even a new profile. The settings
    go in one write (:class:`~untaped.config.use_cases.SetSettings`), which
    refuses a value changed since the form opened; then each secret, a
    declared credential filed under the key just saved. Raises
    :class:`ConfigError` when a write is refused; what was written before
    stays.
    """
    notes: list[Note] = []
    section, profile = spec.name, candidate.profile
    if any(secret.how in ("store", "move") for secret in candidate.secrets):
        preflight()
    notes.extend(ensure_profile(profile))
    repo = SettingsFileRepository()
    if candidate.values:
        # The doctor rows printed on close say what the form saved, so these lines are not kept.
        SetSettings(repo)(
            {f"{section}.{key}": text for key, text in candidate.values},
            profile=profile,
            expected={f"{section}.{key}": own for key, own in candidate.expected},
        )
    marks = service_marks(spec)
    for secret in candidate.secrets:
        marked = next(each for each in marks.secrets if each.key == secret.name)
        if marked.credential is not None:
            notes.extend(_login(marked.credential, candidate, secret, current, repo))
            if secret.how != "keep" and secret.name == "token":
                notes.extend(_retire(section, current.own_command, None))
            continue
        notes.extend(_legacy_token(spec, candidate, secret, current, store, repo))
    return notes


def _legacy_token(
    spec: PluginSpec,
    candidate: Candidate,
    secret: SecretChoice,
    current: ServiceState,
    store: TokenStore | None,
    repo: SettingsFileRepository,
) -> list[Note]:
    """Write the legacy fallback's ``token`` choice to the 11.0 settings, as 11.0 did."""
    notes: list[Note] = []
    section, profile, how = spec.name, candidate.profile, secret.how
    argv = list(secret.argv) if secret.argv is not None else None
    if how in ("store", "move") and store is not None:
        typed = _typed(secret.token) if how == "store" else current.plaintext
        if typed is not None:
            save_token(repo, section, profile, typed, store)
            argv = store.read_argv(entry_name(profile, section))
    elif how == "enter" and secret.token is not None:
        repo.set_value(f"{section}.token", _typed(secret.token) or "", profile=profile)
    elif argv is not None:
        repo.set_value(f"{section}.token_command", json.dumps(argv), profile=profile)
        # A stored token wins over the command; drop it so the command is used.
        repo.unset_value(f"{section}.token", profile=profile)
    elif how == "env":
        # The token and the profile's own command would both win over the variable.
        repo.unset_value(f"{section}.token", profile=profile)
        if current.own_command is not None:
            repo.unset_value(f"{section}.token_command", profile=profile)
            notes.append(("info", f"unset {section}.token_command in profile {profile}"))
        env = token_env_names(settings_model(spec).model_construct())[0]
        notes.append(("info", f"export ${env} in your shell for untaped {spec.name} to use it"))
    if how != "keep":
        notes.extend(_retire(section, current.own_command, argv))
    return notes


def _login(
    credential: Credential[Any],
    candidate: Candidate,
    secret: SecretChoice,
    current: ServiceState,
    repo: SettingsFileRepository,
) -> list[Note]:
    """File a declared credential's choice under the key just saved, as ``auth login`` does.

    The key is read back from the profile once its settings are written, so
    the login is filed under exactly what later runs resolve; a variable
    that sets part of it over the saved settings is named. The profile's
    old ``<name>`` and ``<name>_command`` go: the stored login answers first.
    """
    section, profile, how = candidate.section, candidate.profile, secret.how
    if how == "keep":
        return []
    with profile_scope(profile):
        settings = load_settings_section(section)
    key = credential_key(credential, settings)
    if key is None:
        raise ConfigError(
            f"{section} has no settings to file the {credential.label} under",
            fix_by="user",
        )
    notes: list[Note] = []
    variables = _key_variables(credential, section, type(settings))
    if variables:
        names = ", ".join(f"${name}" for name in variables)
        notes.append(
            (
                "warning",
                f"{names} sets the {section} settings the {credential.label} is filed under, "
                f"so it was filed for {describe_key(key)}",
            )
        )
    if how == "env":
        if credential.forget(key) is not None:
            notes.append(("info", f"forgot the stored {credential.label} for {describe_key(key)}"))
        env = credential.env[0] if credential.env else None
        if env is not None:
            notes.append(("info", f"export ${env} in your shell for untaped {section} to use it"))
    else:
        typed = _typed(secret.token) if how == "store" else current.plaintext
        if how == "command" and secret.argv is not None:
            credential.store(key, command=list(secret.argv))
        elif typed is not None:
            credential.store(key, token=typed)
        else:
            return notes
    for old in (credential.name, f"{credential.name}_command"):
        if repo.unset_value(f"{section}.{old}", profile=profile)[0]:
            notes.append(("info", f"unset {section}.{old} in profile {profile}"))
    return notes


def _key_variables(
    credential: Credential[Any], section: str, model: type[BaseModel]
) -> tuple[str, ...]:
    """The set ``UNTAPED_*`` variables that supply a field of ``credential``'s key."""
    for path, each in key_fields(model):
        if each is not credential.key:
            continue
        found = (env_var_setting((section, *path, name)) for name in each.model_fields)
        return tuple(dict.fromkeys(name for name in found if name is not None))
    return ()


def ensure_profile(profile: str) -> list[Note]:
    """Create ``profile`` when it does not exist yet (``default`` always does); its note."""
    if profile_exists(profile):
        return []
    CreateProfile(ProfileFileRepository())(profile)
    return [("success", f"created profile: {profile}")]


def write_setting(key: str, value: str | None, *, profile: str, expected: object) -> SettingChange:
    """Set ``key`` to ``value`` in ``profile`` as ``config set`` does (``None`` unsets it).

    ``expected`` is the profile's own value when the field opened: a write
    over a value changed since is refused. A profile that does not exist yet
    is created by its first valid write (its note comes first); a value it
    would refuse, or an unset, leaves it uncreated.
    """
    repo = SettingsFileRepository()
    if value is None:
        if not profile_exists(profile):
            return SettingChange(SettingOutcome(key=key, profile=profile, action="unchanged"))
        return UnsetSetting(repo)(key, profile=profile)
    if not profile_exists(profile):
        # A new profile resolves as ``default`` does, so check the value there first.
        SetSetting(repo)(key, value, profile=DEFAULT_PROFILE, dry_run=True)
    created = ensure_profile(profile)
    change = SetSetting(repo)(key, value, profile=profile, expected=expected)
    return replace(change, notes=(*created, *change.notes))


def write_ranking(
    target: RankTarget,
    value: str | None,
    *,
    profile: str,
    owners: Collection[str],
    expected: object = None,
) -> SettingChange:
    """Rank ``target``'s providers in ``profile`` as ``plugin rank`` does.

    ``value`` is the providers as JSON (none, or ``None``, removes the
    ranking), or, for a ``target`` with no method yet, ``CONTRACT METHOD
    PROVIDER...`` as ``plugin rank`` takes it (the contract is ``target``'s
    owner's, its owner named or not). ``expected`` is the profile's own
    ranking when its row opened: a ranking changed since is refused. A new
    profile is created by its first valid ranking.
    """
    if target.method:
        contract, method = f"{target.owner}.{target.contract}", target.method
        plugins = [] if value is None else [str(each) for each in json.loads(value)]
    else:
        try:
            words = shlex.split(value or "")
        except ValueError as exc:
            raise UsageError(f"not a command line: {exc}") from None
        if len(words) < 3:
            raise UsageError("give the contract, the method and its providers")
        contract, method, *plugins = words
        owner, dot, name = contract.rpartition(".")
        if dot and owner != target.owner:
            raise UsageError(f"rank {contract} in {owner}'s section; this is {target.owner}'s")
        contract = f"{target.owner}.{name}"
    rank = RankProviders(SettingsFileRepository(), owners)
    if not profile_exists(profile):
        if not plugins:
            return SettingChange(
                SettingOutcome(key=target.owner, profile=profile, action="unchanged")
            )
        rank(contract, method, plugins, profile=DEFAULT_PROFILE, dry_run=True)
    created = ensure_profile(profile)
    if target.method and value is not None:  # an unset (delete) asks no question first
        change = rank(contract, method, plugins, profile=profile, expected=expected)
    else:
        change = rank(contract, method, plugins, profile=profile)
    return replace(change, notes=(*created, *change.notes))


def _typed(token: SecretStr | None) -> str | None:
    """The typed token's text (the one place it is unwrapped before it is stored)."""
    return None if token is None else token.get_secret_value()


def _retire(section: str, old: list[str] | None, replacement: list[str] | None) -> list[Note]:
    """Delete the entry the profile's replaced preset command read, so none is orphaned."""
    if old is None or old == replacement:
        return []
    try:
        deleted, where = delete_stored_token(old)
    except ConfigError as exc:
        # The new source is already in place; an unreachable old store must
        # not abort setup over a leftover entry.
        preset = preset_entry(old)
        where = preset[0].describe(preset[1]) if preset else f"{section}.token_command"
        return [
            (
                "warning",
                f"could not delete the replaced {section} token from {where} ({exc}); "
                "delete it yourself",
            )
        ]
    if deleted == "deleted":
        return [("info", f"deleted the replaced {section} token from {where}")]
    if deleted == "gone":
        return [("info", f"the replaced {section} token was already gone from {where}")]
    return []
