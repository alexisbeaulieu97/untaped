"""What ``untaped setup`` writes for one service once its check passed (or the user said so).

:func:`write_candidate` writes one service: create the profile once, set
``base_url``, then the branch the user chose for the token (``store``,
``move``, ``enter``, ``command``, ``env`` or ``keep``), retiring the entry a
replaced preset command read. It runs as the screen's write command, so it
prints nothing: its lines come back as notes for the screen to print after it
closes.

:class:`Candidate` is what the user asked for, in one place: the check reads it
as overlay values (:func:`overlay_values`) and the write reads it as branches,
so the two cannot disagree about what "this token source" means. A typed token
is a ``SecretStr`` throughout and is unwrapped only where it is stored.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import SecretStr

from untaped.auth import token_env_names
from untaped.capabilities.registry import CapabilitySpec
from untaped.config.repository import SettingsFileRepository
from untaped.errors import ConfigError
from untaped.management.auth import delete_stored_token, save_token
from untaped.management.setup_state import ServiceState
from untaped.profile.repository import ProfileFileRepository
from untaped.profile.use_cases import CreateProfile
from untaped.profile_resolver import DEFAULT_PROFILE
from untaped.render import MessageKind
from untaped.token_store import TokenStore, entry_name, preset_entry

__all__ = ["Candidate", "Note", "overlay_values", "write_candidate"]

type Note = tuple[MessageKind, str]
"""A line about a write, kept to print after the screen closes."""

type Checked = Literal["passed", "unchecked", "failed"]
"""What the check said about the candidate: it passed, there was none to run, or the user
saved it anyway after it failed."""


@dataclass(frozen=True)
class Candidate:
    """One service's settings as the user entered them, not yet written.

    ``how`` is the token branch (``keep``, ``move``, ``store``, ``enter``,
    ``command``, ``env``); ``token`` is the typed secret of ``store`` and
    ``enter``, ``argv`` the command of ``command``.
    """

    name: str
    section: str
    profile: str
    how: str
    url: str
    token: SecretStr | None = None
    argv: tuple[str, ...] | None = None
    checked: Checked = "unchecked"


def overlay_values(candidate: Candidate, current: ServiceState) -> dict[str, object]:
    """The settings overlay that stands in for ``candidate`` while it is checked.

    A ``None`` unsets the key: a command source removes the profile's token, the
    environment removes both the token and the profile's own command (they
    would win over the variable); ``keep`` changes only the URL.
    """
    values: dict[str, object] = {"base_url": candidate.url}
    match candidate.how:
        case "store" | "enter":
            values["token"] = candidate.token
        case "move":
            values["token"] = None if current.plaintext is None else SecretStr(current.plaintext)
        case "command":
            values["token_command"] = list(candidate.argv or ())
            values["token"] = None
        case "env":
            values["token"] = None
            if current.own_command is not None:
                values["token_command"] = None
    return values


def write_candidate(
    spec: CapabilitySpec,
    candidate: Candidate,
    current: ServiceState,
    store: TokenStore | None,
    preflight: Callable[[], None],
) -> list[Note]:
    """Write ``candidate``; return the lines to print about it.

    The store is tested first (``preflight``, once per run), so a store that
    cannot work leaves nothing behind, not even a new profile. Raises
    :class:`ConfigError` when a write is refused; what was written before stays.
    """
    notes: list[Note] = []
    section, profile, how = spec.config_section, candidate.profile, candidate.how
    if how in ("store", "move"):
        preflight()
    profiles = ProfileFileRepository()
    if profile != DEFAULT_PROFILE and profiles.read(profile) is None:
        CreateProfile(profiles)(profile)
        notes.append(("success", f"created profile: {profile}"))
    repo = SettingsFileRepository()
    argv = list(candidate.argv) if candidate.argv is not None else None
    repo.set_value(f"{section}.base_url", candidate.url, profile=profile)
    if how in ("store", "move") and store is not None:
        secret = _typed(candidate.token) if how == "store" else current.plaintext
        if secret is not None:
            save_token(repo, section, profile, secret, store)
            argv = store.read_argv(entry_name(profile, section))
    elif how == "enter" and candidate.token is not None:
        repo.set_value(f"{section}.token", _typed(candidate.token) or "", profile=profile)
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
        env = token_env_names(spec.profile_model.model_construct())[0]
        notes.append(("info", f"export ${env} in your shell for untaped {spec.name} to use it"))
    if how != "keep":
        notes.extend(_retire(section, current.own_command, argv))
    return notes


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
