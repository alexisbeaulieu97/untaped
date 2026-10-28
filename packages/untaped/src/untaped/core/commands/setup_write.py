"""What ``untaped setup`` writes: a plugin's setup form, or one setting from its row.

:func:`write_setting` writes one setting through the use case ``config set``
runs (creating the profile first when it is new). :func:`write_candidate`
writes one plugin's form once its check passed (or the user said so): create
the profile once, write the settings the user changed in one change
(:class:`~untaped.core.config.use_cases.SetSettings`), then each secret's branch
(``store``, ``command``, ``env`` or ``keep``), filed as ``auth login`` files
it. Both run as the screen's write
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

from untaped.core.commands.setup_state import ServiceState, profile_exists, service_marks
from untaped.core.config.models import SettingOutcome
from untaped.core.config.repository import SettingsFileRepository
from untaped.core.config.use_cases import (
    Note,
    RankProviders,
    SetSetting,
    SetSettings,
    SettingChange,
    UnsetSetting,
)
from untaped.core.profile.repository import ProfileFileRepository
from untaped.core.profile.use_cases import CreateProfile
from untaped.runtime.profile_resolver import DEFAULT_PROFILE
from untaped.runtime.registry import PluginSpec
from untaped.runtime.run import child_run
from untaped.runtime.settings import env_var_setting, load_settings_section
from untaped.sdk.errors import ConfigError, UsageError
from untaped.sdk.network.credentials import Credential, credential_key, describe_key, key_fields

__all__ = [
    "Candidate",
    "RankTarget",
    "SecretChoice",
    "ensure_profile",
    "landed",
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

    ``name`` is the credential's; ``how`` is the tab (``keep``, ``store``,
    ``command``, ``env``); ``token`` is the typed secret of ``store``,
    ``argv`` the command of ``command``.
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


def overlay_values(candidate: Candidate) -> dict[str, object]:
    """The settings overlay that stands in for ``candidate`` while it is checked.

    Each changed setting as the value a write would store; then each secret's
    choice, under the credential's name (a typed secret) or ``<name>_command``
    (an argv), where :meth:`~untaped.sdk.network.credentials.Credential.get` reads its
    candidate; a ``None`` name stands for a variable, ``keep`` for nothing.
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
            case "store":
                values[name] = secret.token
            case "command":
                values[command] = list(secret.argv or ())
                values[name] = None
            case "env":
                values[name] = None
    return values


def landed(candidate: Candidate, state: ServiceState) -> tuple[str, ...]:
    """The keys of ``candidate``'s settings that ``state`` (read after a write) holds as asked."""
    repo = SettingsFileRepository()
    section = candidate.section
    return tuple(
        key
        for key, text in candidate.values
        if state.own.get(key) == (None if text is None else repo.coerced(f"{section}.{key}", text))
    )


def write_candidate(
    spec: PluginSpec,
    candidate: Candidate,
    preflight: Callable[[], None],
) -> list[Note]:
    """Write ``candidate``; return the lines to print about it.

    The store is tested first (``preflight``, once per run), so a store that
    cannot work leaves nothing behind, not even a new profile. The settings
    go in one write (:class:`~untaped.core.config.use_cases.SetSettings`), which
    refuses a value changed since the form opened; then each secret, a
    declared credential filed under the key just saved. Raises
    :class:`ConfigError` when a write is refused; what was written before
    stays.
    """
    notes: list[Note] = []
    section, profile = spec.name, candidate.profile
    if any(secret.how == "store" for secret in candidate.secrets):
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
        assert marked.credential is not None, "every secret is a declared credential"
        notes.extend(_login(marked.credential, candidate, secret))
    return list(dict.fromkeys(notes))  # a variable over a shared key is named once


def _login(credential: Credential[Any], candidate: Candidate, secret: SecretChoice) -> list[Note]:
    """File a declared credential's choice under the key just saved, as ``auth login`` does.

    The key is read back from the profile once its settings are written, so
    the login is filed under exactly what later runs resolve; a variable
    that sets part of it over the saved settings is named.
    """
    section, profile, how = candidate.section, candidate.profile, secret.how
    if how == "keep":
        return []
    with child_run(profiles=profile):
        settings = load_settings_section(section)
    key = credential_key(credential, settings)
    if key is None:
        raise ConfigError(
            f"{section} has no settings to file the {credential.label} under",
            fix_by="user",
        )
    notes: list[Note] = []
    variables = _key_variables(credential, section, type(settings))
    if variables and how != "env":
        names = ", ".join(f"${name}" for name in variables)
        verb = "sets" if len(variables) == 1 else "set"
        notes.append(
            (
                "warning",
                f"{names} {verb} part of the {section} key, so this save filed its logins "
                f"for {describe_key(key)}",
            )
        )
    if how == "env":
        if credential.forget(key) is not None:
            notes.append(("info", f"forgot the stored {credential.label} for {describe_key(key)}"))
        env = credential.env[0] if credential.env else None
        if env is not None:
            notes.append(("info", f"export ${env} in your shell for untaped {section} to use it"))
    else:
        typed = _typed(secret.token)
        if how == "command" and secret.argv is not None:
            credential.store(key, command=list(secret.argv))
        elif typed is not None:
            credential.store(key, token=typed)
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
