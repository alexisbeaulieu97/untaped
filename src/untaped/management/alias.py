"""Root ``untaped alias`` command group: per-profile command aliases.

Aliases live in the shell's own settings section, ``shell.aliases``: a
mapping of alias name to the argv it stands for (no shell ever runs it).
``alias set``/``remove`` rewrite the target profile's own mapping (the
active profile, or the root ``--profile``) through the validated
``config set`` path; ``alias list`` shows the effective mapping, where
``profiles.default`` aliases merge beneath the active profile's. The root
dispatcher expands ``untaped NAME [ARGS…]`` (:func:`untaped._root_options.expand_alias`)
only when ``NAME`` is not a built-in command, and never expands twice.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable
from typing import Annotated, Any

from cyclopts import App, Parameter

from untaped.cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    YesOption,
    create_app,
    emit,
    report_errors,
)
from untaped.config.repository import SettingsFileRepository
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError, UsageError
from untaped.messages import not_found, q
from untaped.profile_resolver import DEFAULT_PROFILE, effective_active_profile_name
from untaped.records import OutcomeRecord, Record
from untaped.settings import load_settings_section
from untaped.ui import ui_context

#: An alias name: lowercase letters, digits and dashes, like a command name.
ALIAS_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ALIASES_KEY = "shell.aliases"
_OUTCOME = "untaped.alias_outcome"


class AliasRow(Record):
    """One effective alias (kind ``untaped.alias``)."""

    name: str
    command: str
    """The argv the alias stands for, shell-quoted for reading."""
    profile: str
    """The profile that defines it (``default`` or the active one)."""


class AliasOutcome(OutcomeRecord):
    """The result of ``alias set``/``remove`` (kind ``untaped.alias_outcome``).

    ``action`` is ``created``, ``updated``, ``unchanged``, ``deleted``, or
    ``planned`` under ``--dry-run``.
    """

    name: str
    profile: str


def check_aliases(value: dict[str, list[str]]) -> dict[str, list[str]]:
    """Validate an ``aliases`` mapping (names and non-empty argv)."""
    for name, argv in value.items():
        if not ALIAS_NAME.match(name):
            raise ValueError(f"alias name must be lowercase letters, digits and dashes: {q(name)}")
        if not argv:
            raise ValueError(f"alias {q(name)} has no command")
    return value


def build_root_alias_app(*, is_builtin: Callable[[str], bool]) -> App:
    """Return the root ``alias`` group; ``is_builtin`` says if a name is a root command."""
    app = create_app(name="alias", help="Manage command aliases (``untaped NAME [ARGS…]``).")

    @app.command(name="set")
    def set_command(
        name: Annotated[str, Parameter(help="Alias name (lowercase letters, digits, dashes).")],
        /,
        *command: Annotated[
            str,
            Parameter(
                help="Command and arguments the alias runs; put them after `--`.",
                allow_leading_hyphen=True,
            ),
        ],
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Save ``untaped NAME`` as a shortcut for ``untaped COMMAND…`` in the profile."""
        with report_errors():
            _check_name(name, is_builtin)
            if not command:
                raise UsageError("alias set requires a command after NAME (`-- COMMAND ARGS…`)")
            profile, own = _own_aliases()
            argv = list(command)
            if own.get(name) == argv:
                action = "unchanged"
            else:
                SettingsFileRepository().set_value(
                    _ALIASES_KEY, json.dumps({**own, name: argv}), dry_run=dry_run
                )
                action = "planned" if dry_run else "updated" if name in own else "created"
                if not dry_run:
                    ui_context(strict=False).success(
                        f"alias {name} = {shlex.join(argv)} (profile {profile})"
                    )
            emit(
                AliasOutcome(name=name, profile=profile, action=action),
                fmt=fmt,
                columns=columns,
                kind=_OUTCOME,
            )

    @app.command(name="list")
    def list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
        """List the aliases in effect for the profile."""
        with report_errors():
            emit(
                _alias_rows(),
                fmt=fmt,
                columns=columns,
                kind="untaped.alias",
                empty="No aliases found.",
            )

    @app.command(name="remove")
    def remove_command(
        name: Annotated[str, Parameter(help="Alias to remove from the profile.")],
        /,
        *,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Remove an alias from the profile that defines it."""
        with report_errors():
            profile, own = _own_aliases()
            if name not in own:
                raise ConfigError(not_found("alias", name, known=sorted(own)))
            action = "planned"
            if not dry_run:
                ui_context(strict=False).confirm_or_cancel(
                    f"Remove alias {q(name)} from profile {profile}?",
                    assume_yes=yes,
                    refusal="alias remove requires --yes when not interactive",
                )
                remaining = {key: argv for key, argv in own.items() if key != name}
                repo = SettingsFileRepository()
                if remaining:
                    repo.set_value(_ALIASES_KEY, json.dumps(remaining))
                else:
                    repo.unset_value(_ALIASES_KEY)
                ui_context(strict=False).success(f"removed alias {name} (profile {profile})")
                action = "deleted"
            emit(
                AliasOutcome(name=name, profile=profile, action=action),
                fmt=fmt,
                columns=columns,
                kind=_OUTCOME,
            )

    return app


def _check_name(name: str, is_builtin: Callable[[str], bool]) -> None:
    if not ALIAS_NAME.match(name):
        raise UsageError(f"alias name must be lowercase letters, digits and dashes: {q(name)}")
    if is_builtin(name):
        raise UsageError(f"alias {q(name)} would shadow the built-in command {q(name)}")


def _own_aliases() -> tuple[str, dict[str, list[str]]]:
    """The write profile's name and the aliases it defines itself (not inherited)."""
    profile = effective_active_profile_name(read_config_dict()) or DEFAULT_PROFILE
    data = SettingsFileRepository().profile_data(profile) or {}
    shell = data.get("shell")
    aliases = shell.get("aliases") if isinstance(shell, dict) else None
    if not isinstance(aliases, dict):
        return profile, {}
    return profile, {str(key): list(value) for key, value in aliases.items()}


def _alias_rows() -> list[AliasRow]:
    aliases: dict[str, list[str]] = load_settings_section("shell").aliases
    provenance = SettingsFileRepository().provenance()
    return [
        AliasRow(
            name=name,
            command=shlex.join(argv),
            profile=_defining_profile(provenance, name),
        )
        for name, argv in sorted(aliases.items())
    ]


def _defining_profile(provenance: dict[tuple[str, ...], Any], name: str) -> str:
    source = provenance.get(("shell", "aliases", name))
    return str(source) if source is not None else "env"


__all__ = ["ALIAS_NAME", "AliasOutcome", "AliasRow", "build_root_alias_app", "check_aliases"]
