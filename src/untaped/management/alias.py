"""Root ``untaped alias`` command group: per-profile command aliases.

Aliases live in the shell's own settings section, ``shell.aliases``: a
mapping of alias name to the argv it stands for (no shell ever runs it).
``alias set``/``remove`` read and rewrite the target profile's own mapping
(the active profile, or the root ``--profile``) inside one locked, validated
config mutation, so overlapping commands never drop each other's aliases;
``alias list`` shows the effective mapping, where ``profiles.default``
aliases merge beneath the active profile's. The root
dispatcher expands ``untaped NAME [ARGS…]`` (:func:`untaped._root_options.expand_alias`)
only when ``NAME`` is not a built-in command, and never expands twice. Name
and argv rules live with the settings model in :mod:`untaped.shell_settings`.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from typing import Annotated, Any, ClassVar

from cyclopts import App, Parameter
from pydantic import ValidationError

from untaped.cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    YesOption,
    create_app,
    emit,
    report_errors,
    writes,
)
from untaped.config.repository import SettingsFileRepository
from untaped.config_file import read_config_dict
from untaped.errors import ConfigError, UsageError, first_validation_error
from untaped.messages import hint, not_found, q
from untaped.profile_resolver import DEFAULT_PROFILE, effective_active_profile_name
from untaped.records import OutcomeRecord, Record
from untaped.settings import load_settings_section
from untaped.shell_settings import ShellProfileSettings, alias_name_error
from untaped.ui import ui_context

_ALIASES_KEY = "shell.aliases"
_OUTCOME = "untaped.alias_outcome"


class AliasRow(Record):
    """One effective alias (kind ``untaped.alias``)."""

    table_columns: ClassVar[tuple[str, ...]] = ("name", "command", "profile")

    name: str
    command: str
    """The argv the alias stands for, shell-quoted for reading."""
    argv: list[str]
    """The argv the alias stands for."""
    profile: str
    """The profile that defines it (``default`` or the active one)."""


class AliasOutcome(OutcomeRecord):
    """The result of ``alias set``/``remove`` (kind ``untaped.alias_outcome``).

    ``action`` is ``created``, ``updated``, ``unchanged``, ``deleted``, or
    ``planned`` under ``--dry-run``.
    """

    name: str
    profile: str


def build_root_alias_app(*, builtin_for: Callable[[str], str | None]) -> App:
    """Return the root ``alias`` group; ``builtin_for`` names the root command a word selects."""
    app = create_app(name="alias", help="Manage command aliases (``untaped NAME [ARGS…]``).")

    @app.command(name="set")
    @writes
    def set_command(
        name: Annotated[str, Parameter(help="Alias name (lowercase letters, digits, dashes).")],
        /,
        *command: Annotated[
            str,
            # No leading hyphens: root options before `--` (`--profile`, `-v`)
            # apply to this command; only the tokens after `--` are the alias.
            Parameter(help="Command and arguments the alias runs; put them after `--`."),
        ],
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Save ``untaped NAME`` as a shortcut for ``untaped COMMAND…`` in the profile."""
        with report_errors():
            _check_name(name, builtin_for)
            if not command:
                raise UsageError("alias set requires a command after NAME (`-- COMMAND ARGS…`)")
            argv = list(command)
            action = "unchanged"

            def _set(target: str, current: Any) -> str:
                nonlocal action
                own = _parse_aliases(target, current)
                if own.get(name) != argv:
                    action = "updated" if name in own else "created"
                return json.dumps({**own, name: argv})

            profile = SettingsFileRepository().update_value(_ALIASES_KEY, _set, dry_run=dry_run)
            if action != "unchanged":
                if dry_run:
                    action = "planned"
                else:
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
    @writes(destructive=True)
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
                raise ConfigError(_missing_alias(name, profile, own), category="not_found")
            action = "planned"
            if not dry_run:
                ui_context(strict=False).confirm_or_cancel(
                    f"Remove alias {q(name)} from profile {profile}?",
                    assume_yes=yes,
                    refusal="alias remove requires --yes when not interactive",
                )
                SettingsFileRepository().update_value(_ALIASES_KEY, _without(name))
                ui_context(strict=False).success(f"removed alias {name} (profile {profile})")
                action = "deleted"
            emit(
                AliasOutcome(name=name, profile=profile, action=action),
                fmt=fmt,
                columns=columns,
                kind=_OUTCOME,
            )

    return app


def _check_name(name: str, builtin_for: Callable[[str], str | None]) -> None:
    error = alias_name_error(name)
    if error is not None:
        raise UsageError(error)
    builtin = builtin_for(name)
    if builtin is not None:
        raise UsageError(f"alias {q(name)} would shadow the built-in command {q(builtin)}")


def _without(name: str) -> Callable[[str, Any], str | None]:
    """The ``alias remove`` update: the target's aliases minus ``name`` (``None`` when empty)."""

    def _remove(target: str, current: Any) -> str | None:
        own = _parse_aliases(target, current)
        if name not in own:
            raise ConfigError(_missing_alias(name, target, own), category="not_found")
        remaining = {key: argv for key, argv in own.items() if key != name}
        return json.dumps(remaining) if remaining else None

    return _remove


def _missing_alias(name: str, profile: str, own: dict[str, list[str]]) -> str:
    """Not found in ``profile`` itself; point at the profile it is inherited from."""
    source = SettingsFileRepository().provenance().get(("shell", "aliases", name))
    if source is not None and source != profile:
        return (
            f"alias {q(name)} is defined in profile {source}, not {profile}\n"
            f"{hint(f'--profile {source} alias remove {name}')}"
        )
    return not_found("alias", name, known=sorted(own))


def _own_aliases() -> tuple[str, dict[str, list[str]]]:
    """The write profile's name and the aliases it defines itself (not inherited)."""
    profile = effective_active_profile_name(read_config_dict()) or DEFAULT_PROFILE
    data = SettingsFileRepository().profile_data(profile) or {}
    shell = data.get("shell")
    return profile, _parse_aliases(
        profile, shell.get("aliases") if isinstance(shell, dict) else None
    )


def _parse_aliases(profile: str, aliases: Any) -> dict[str, list[str]]:
    """Validate the raw ``shell.aliases`` a profile defines itself (``None`` when unset)."""
    if aliases is None:
        return {}
    try:
        settings = ShellProfileSettings.model_validate({"aliases": aliases})
    except ValidationError as exc:
        raise ConfigError(
            f"invalid {_ALIASES_KEY} in profile {profile}: {first_validation_error(exc)}"
        ) from exc
    return settings.aliases


def _alias_rows() -> list[AliasRow]:
    aliases: dict[str, list[str]] = load_settings_section("shell").aliases
    provenance = SettingsFileRepository().provenance()
    return [
        AliasRow(
            name=name,
            command=shlex.join(argv),
            argv=argv,
            profile=_defining_profile(provenance, name),
        )
        for name, argv in sorted(aliases.items())
    ]


def _defining_profile(provenance: dict[tuple[str, ...], Any], name: str) -> str:
    source = provenance.get(("shell", "aliases", name))
    return str(source) if source is not None else "env"


__all__ = ["AliasOutcome", "AliasRow", "build_root_alias_app"]
