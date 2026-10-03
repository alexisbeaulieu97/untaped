"""Root ``untaped auth …`` command group: keep API tokens out of ``config.yml``.

``auth set`` stores a token with a tool on this machine
(:mod:`untaped.token_store`) and writes the ``<section>.token_command``
that reads it back; ``auth migrate`` does the same for every token stored in
plain text; ``auth unset`` removes what ``auth set`` stored; ``auth status``
says where each token comes from without running anything that reads one.
Tokens are read from a hidden prompt or stdin, never from an argument.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from cyclopts import App, Parameter

from untaped.auth import takes_token_command, token_env_names, token_override_env
from untaped.capabilities.registry import CapabilitySpec, CompositionResult
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
from untaped.errors import ConfigError
from untaped.messages import hint, plural
from untaped.profile_resolver import (
    DEFAULT_PROFILE,
    effective_active_profile_name,
    resolve_profiles,
)
from untaped.settings import active_settings_layout
from untaped.stdin import read_stdin_text
from untaped.token_store import (
    StoreName,
    TokenStore,
    entry_name,
    no_store_message,
    pick_store,
    preset_entry,
    usable_stores,
)
from untaped.ui import UiContext, ui_context

StoreOption = Annotated[
    StoreName | None,
    Parameter(name="--store", help="Store to use; default: the first usable one."),
]


@dataclass(frozen=True)
class _TokenSection:
    """A composed section that takes ``token`` and ``token_command``."""

    section: str
    env: tuple[str, ...]
    """Its conventional token environment variables, in order."""


def _token_sections(result: CompositionResult) -> dict[str, _TokenSection]:
    """Every composed section whose profile model has ``token`` and ``token_command``."""
    return {
        registered.spec.config_section: _token_section(registered.spec)
        for registered in result.capabilities
        if "token" in registered.spec.profile_model.model_fields
        and takes_token_command(registered.spec.profile_model)
    }


def _token_section(spec: CapabilitySpec) -> _TokenSection:
    model = spec.profile_model
    return _TokenSection(spec.config_section, token_env_names(model.model_construct()))


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


def inherited_from_default(section: str, profile: str, key: str) -> bool:
    """Whether ``profile`` would inherit ``<section>.<key>`` from ``default``."""
    if profile == DEFAULT_PROFILE:
        return False
    default = active_settings_layout().profile_data(read_config_dict(), DEFAULT_PROFILE) or {}
    node = default.get(section)
    return isinstance(node, dict) and bool(node.get(key))


def refuse_inherited_token(section: str, profile: str) -> None:
    """Raise when ``default``'s plaintext token would win over ``profile``'s command."""
    if inherited_from_default(section, profile, "token"):
        raise ConfigError(
            f"{section}.token is set in profile {DEFAULT_PROFILE} and would override the "
            f"token command in profile {profile}; move it out of {DEFAULT_PROFILE} first\n"
            f"{hint('auth migrate')}"
        )


def drop_token_command(
    repo: SettingsFileRepository, section: str, profile: str, argv: Sequence[str]
) -> str | None:
    """Unset ``profile``'s own ``<section>.token_command``, deleting what it reads.

    The entry is deleted only when untaped wrote the command (a preset); an
    entry that is already gone is not an error. Returns the deleted entry's
    description, or ``None`` for a command untaped did not write.
    """
    preset = preset_entry(argv)
    where = None
    if preset is not None:
        store, entry = preset
        where = store.describe(entry)
        try:
            store.delete(entry)
        except ConfigError:
            if store.has(entry):
                raise
            message = f"the {section} token was already gone from {where}"
            ui_context(strict=False).message("info", message)
    repo.unset_value(f"{section}.token_command", profile=profile)
    return where


def plaintext_token(data: dict[str, Any] | None, section: str) -> str | None:
    """The plaintext ``<section>.token`` in one profile's own ``data``, if any."""
    node = (data or {}).get(section)
    token = node.get("token") if isinstance(node, dict) else None
    return token.strip() if isinstance(token, str) and token.strip() else None


def build_root_auth_app(*, result: CompositionResult) -> App:
    """Return the root ``auth`` command group for one composition."""
    app = create_app(name="auth", help="Keep API tokens out of ``config.yml``.")

    @app.command(name="set")
    @writes
    def set_command(
        section: Annotated[str, Parameter(help="Section whose token to store, e.g. awx.")],
        /,
        *,
        stdin: Annotated[
            bool, Parameter(name="--stdin", negative="", help="Read the token from stdin.")
        ] = False,
        store: StoreOption = None,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Store a token with this machine's password store and point the config at it.

        Tries macOS ``security``, ``secret-tool`` (Secret Service), then
        ``pass``. The token is read from a hidden prompt (or ``--stdin``),
        stored on the tool's stdin, read back, and only then written as
        ``<section>.token_command`` in the active profile (or the root
        ``--profile``), replacing any plaintext ``<section>.token``.
        Running it again replaces the stored token.
        """
        with report_errors():
            _set(result, section, stdin=stdin, store=store, fmt=fmt, columns=columns)

    @app.command(name="unset")
    @writes(destructive=True)
    def unset_command(
        section: Annotated[str, Parameter(help="Section whose stored token to remove.")],
        /,
        *,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Delete the token ``auth set`` stored and unset ``<section>.token_command``.

        Acts on the active profile's own setting only (or the root
        ``--profile``); an inherited one is left alone. A ``token_command``
        untaped did not write is left alone too.
        """
        with report_errors():
            _unset(result, section, yes=yes, dry_run=dry_run, fmt=fmt, columns=columns)

    @app.command(name="status")
    def status_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
        """Show where each profile's tokens come from, and which stores work here.

        Never runs a ``token_command``, so it cannot tell whether a stored
        entry still exists; ``untaped doctor --online`` checks that.
        """
        with report_errors():
            _status(result, fmt=fmt, columns=columns)

    @app.command(name="migrate")
    @writes
    def migrate_command(
        *,
        store: StoreOption = None,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Move every plaintext token in ``config.yml``, in every profile, to a store.

        Each token is stored and read back before it is removed from the
        file; one that fails stays where it was, and the command exits 1.
        """
        with report_errors():
            _migrate(result, store=store, dry_run=dry_run, fmt=fmt, columns=columns)

    return app


def _section(result: CompositionResult, section: str) -> _TokenSection:
    sections = _token_sections(result)
    if section not in sections:
        known = ", ".join(sorted(sections)) or "(none)"
        raise ConfigError(f"unknown token section {section!r}; known: {known}", category="invalid")
    return sections[section]


def _target_profile() -> str:
    return effective_active_profile_name(read_config_dict()) or DEFAULT_PROFILE


def _set(
    result: CompositionResult,
    section: str,
    *,
    stdin: bool,
    store: StoreName | None,
    fmt: Any,
    columns: list[str] | None,
) -> None:
    spec = _section(result, section)
    profile = _target_profile()
    entry_name(profile, section)
    refuse_inherited_token(section, profile)
    chosen = pick_store(store)
    if chosen is None:
        raise ConfigError(no_store_message(section, spec.env), category="unavailable")
    ui = ui_context(strict=False)
    token = _read_stdin_token() if stdin else _prompt_token(ui, section)
    where = save_token(SettingsFileRepository(), section, profile, token, chosen)
    ui.success(
        f"stored the {section} token in {where}; {section}.token_command set in profile {profile}"
    )
    _warn_env_override(ui, section)
    emit(
        {"section": section, "profile": profile, "store": where, "action": "stored"},
        fmt=fmt,
        columns=columns,
        kind=_AUTH_OUTCOME,
    )


def _read_stdin_token() -> str:
    token = read_stdin_text().strip()
    if not token:
        raise ConfigError("no token received on stdin", category="invalid")
    if "\n" in token or "\r" in token:
        raise ConfigError("--stdin expects the token on one line", category="invalid")
    return token


def _prompt_token(ui: UiContext, section: str) -> str:
    with ui.terminal(refusal="no terminal to prompt for the token; pipe it with --stdin"):
        return ui.secret(f"{section} token").strip()


def _warn_env_override(ui: UiContext, section: str) -> None:
    override = token_override_env(section)
    if override is not None:
        ui.message(
            "warning",
            f"${override} is set and wins over the stored token until you unset it",
        )


def _unset(
    result: CompositionResult,
    section: str,
    *,
    yes: bool,
    dry_run: bool,
    fmt: Any,
    columns: list[str] | None,
) -> None:
    _section(result, section)
    profile = _target_profile()
    raw = read_config_dict()
    own = (active_settings_layout().profile_data(raw, profile) or {}).get(section)
    argv = own.get("token_command") if isinstance(own, dict) else None
    ui = ui_context(strict=False)
    row = {"section": section, "profile": profile, "store": None, "action": "unchanged"}
    if not argv:
        _, provenance = resolve_profiles(raw, active_override=profile)
        holder = provenance.get((section, "token_command"))
        if holder is not None and holder != profile:
            fix = hint(f"--profile {holder} auth unset {section}")
            ui.message(
                "info",
                f"{section}.token_command is inherited from profile {holder}; "
                f"profile {profile} is unchanged\n{fix}",
            )
        else:
            ui.message("info", f"{section}.token_command is not set in profile {profile}")
        emit(row, fmt=fmt, columns=columns, kind=_AUTH_OUTCOME)
        return
    preset = preset_entry(argv)
    if preset is None:
        raise ConfigError(
            f"{section}.token_command in profile {profile} was not written by untaped; "
            f"remove it yourself\n{hint(f'config unset {section}.token_command')}",
            category="invalid",
        )
    where = preset[0].describe(preset[1])
    row["store"] = where
    if dry_run:
        emit({**row, "action": "planned"}, fmt=fmt, columns=columns, kind=_AUTH_OUTCOME)
        return
    ui.confirm_or_cancel(
        f"Delete the {section} token from {where} and unset {section}.token_command "
        f"in profile {profile}?",
        assume_yes=yes,
    )
    drop_token_command(SettingsFileRepository(), section, profile, argv)
    ui.success(f"deleted the {section} token from {where} (profile {profile})")
    emit({**row, "action": "deleted"}, fmt=fmt, columns=columns, kind=_AUTH_OUTCOME)


def _status(result: CompositionResult, *, fmt: Any, columns: list[str] | None) -> None:
    raw = read_config_dict()
    layout = active_settings_layout()
    profiles = layout.profile_names(raw) or [DEFAULT_PROFILE]
    sections = _token_sections(result).values()
    rows: list[dict[str, object]] = []
    for profile in profiles:
        effective, provenance = resolve_profiles(raw, active_override=profile)
        for spec in sections:
            node = effective.get(spec.section)
            source, key = _source(spec, node if isinstance(node, dict) else {})
            rows.append(
                {
                    "profile": profile,
                    "section": spec.section,
                    "source": source,
                    "set_in": provenance.get((spec.section, key)) if key else None,
                }
            )
    stores = ", ".join(store.name for store in usable_stores()) or "none"
    ui_context(strict=False).message("info", f"token stores usable here: {stores}")
    emit(rows, fmt=fmt, columns=columns, kind="untaped.token_source")


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
    fmt: Any,
    columns: list[str] | None,
) -> None:
    raw = read_config_dict()
    layout = active_settings_layout()
    names = layout.profile_names(raw)
    # `default` first: its token would otherwise win over another profile's command.
    ordered = sorted(names, key=lambda name: name != DEFAULT_PROFILE)
    sections = _token_sections(result)
    pending = [
        (profile, section, token)
        for profile in ordered
        for section in sections
        if (token := plaintext_token(layout.profile_data(raw, profile), section)) is not None
    ]
    ui = ui_context(strict=False)
    if not pending:
        ui.message("info", "no plaintext tokens in the config; nothing to move")
        emit([], fmt=fmt, columns=columns, kind=_AUTH_OUTCOME)
        return
    chosen = pick_store(store)
    if chosen is None:
        raise ConfigError(
            no_store_message(pending[0][1], sections[pending[0][1]].env),
            category="unavailable",
        )
    rows = _move_all(pending, chosen, dry_run=dry_run)
    emit(rows, fmt=fmt, columns=columns, kind=_AUTH_OUTCOME)
    failed = sum(row["action"] == "failed" for row in rows)
    if failed:
        raise ConfigError(f"{plural(failed, 'token')} could not be moved and stay in the config")
    if not dry_run:
        ui.success(f"moved {plural(len(rows), 'token')} to {chosen.name}")


def _move_all(
    pending: Sequence[tuple[str, str, str]], store: TokenStore, *, dry_run: bool
) -> list[dict[str, object]]:
    repo = SettingsFileRepository()
    rows: list[dict[str, object]] = []
    for profile, section, token in pending:
        row: dict[str, object] = {"section": section, "profile": profile}
        try:
            entry = entry_name(profile, section)
            if dry_run:
                rows.append({**row, "store": store.describe(entry), "action": "planned"})
                continue
            where = save_token(repo, section, profile, token, store)
        except ConfigError as exc:
            rows.append({**row, "store": None, "action": "failed", "detail": str(exc)})
            continue
        rows.append({**row, "store": where, "action": "moved"})
    return rows


_AUTH_OUTCOME = "untaped.auth_outcome"


__all__ = [
    "build_root_auth_app",
    "drop_token_command",
    "inherited_from_default",
    "plaintext_token",
    "refuse_inherited_token",
    "save_token",
]
