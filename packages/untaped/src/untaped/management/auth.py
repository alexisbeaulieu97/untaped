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
from functools import partial
from typing import Annotated, Any, Literal

from cyclopts import App, Parameter

from untaped.auth import takes_token_command, token_env_names, token_override_env
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
from untaped.errors import ConfigError, most_severe
from untaped.management.records import AuthOutcome, TokenSource
from untaped.messages import hint, plural, run_hint
from untaped.output.diagnostic import Failure
from untaped.output.sink import Diagnostics
from untaped.plugins.registry import CompositionResult, settings_model
from untaped.profile_resolver import (
    DEFAULT_PROFILE,
    resolve_profiles,
    selected_profile,
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

_diag = Diagnostics()

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
    """Every composed section whose settings model has ``token`` and ``token_command``."""
    models = {
        registered.spec.name: settings_model(registered.spec) for registered in result.plugins
    }
    return {
        name: _TokenSection(name, token_env_names(model.model_construct()))
        for name, model in models.items()
        if "token" in model.model_fields and takes_token_command(model)
    }


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
            f"{section}.token is set in profile {DEFAULT_PROFILE} and would override the "
            f"token command in profile {profile}; move it out of {DEFAULT_PROFILE} first\n"
            f"{hint('auth migrate')}"
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
        entry still exists; ``untaped doctor --online`` checks that. A token
        from a service's conventional variable (such as ``$GITHUB_TOKEN``)
        belongs to no profile.
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
        file; one that fails stays where it was, and the command exits
        non-zero, with the code of the most severe failure's category. It
        prints no token. A keychain may show an unlock prompt on the user's
        screen; a locked `pass` store exits 5 with a hint naming the unlock
        command.
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
    return selected_profile(read_config_dict())


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
    chosen.preflight()
    ui = ui_context(strict=False)
    token = _read_stdin_token() if stdin else _prompt_token(ui, section)
    where = save_token(SettingsFileRepository(), section, profile, token, chosen)
    _diag.success(
        f"stored the {section} token in {where}; {section}.token_command set in profile {profile}"
    )
    _warn_env_override(section)
    emit(
        AuthOutcome(section=section, profile=profile, store=where, action="stored"),
        fmt=fmt,
        columns=columns,
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
    row = AuthOutcome(section=section, profile=profile, store=None, action="unchanged")
    if not argv:
        _, provenance = resolve_profiles(raw, active_override=profile)
        holder = provenance.get((section, "token_command"))
        if holder is not None and holder != profile:
            _diag.info(
                f"{section}.token_command is inherited from profile {holder}; "
                f"profile {profile} is unchanged"
            )
            _diag.hint(run_hint(f"--profile {holder} auth unset {section}"))
        else:
            _diag.info(f"{section}.token_command is not set in profile {profile}")
        emit(row, fmt=fmt, columns=columns)
        return
    preset = preset_entry(argv)
    if preset is None:
        raise ConfigError(
            f"{section}.token_command in profile {profile} was not written by untaped; "
            f"remove it yourself\n{hint(f'config unset {section}.token_command')}",
            category="invalid",
        )
    where = preset[0].describe(preset[1])
    row = row.model_copy(update={"store": where})
    if dry_run:
        emit(row.model_copy(update={"action": "planned"}), fmt=fmt, columns=columns)
        return
    ui.confirm_or_cancel(
        f"Delete the {section} token from {where} and unset {section}.token_command "
        f"in profile {profile}?",
        assume_yes=yes,
    )
    deleted, _ = delete_stored_token(argv)
    SettingsFileRepository().unset_value(f"{section}.token_command", profile=profile)
    if deleted == "gone":
        _diag.success(
            f"the {section} token was already gone from {where}; "
            f"unset {section}.token_command (profile {profile})"
        )
    else:
        _diag.success(f"deleted the {section} token from {where} (profile {profile})")
    emit(row.model_copy(update={"action": deleted}), fmt=fmt, columns=columns)


def _status(result: CompositionResult, *, fmt: Any, columns: list[str] | None) -> None:
    raw = read_config_dict()
    layout = active_settings_layout()
    profiles = layout.profile_names(raw) or [DEFAULT_PROFILE]
    sections = _token_sections(result).values()
    rows: list[TokenSource] = []
    for profile in profiles:
        effective, provenance = resolve_profiles(raw, active_override=profile)
        for spec in sections:
            node = effective.get(spec.section)
            source, key = _source(spec, node if isinstance(node, dict) else {})
            # Other profiles inherit `default`; list them only for a token of their own
            # (one that default's plaintext token shadows drops out too).
            if profile != DEFAULT_PROFILE and (
                key is None or provenance.get((spec.section, key)) != profile
            ):
                continue
            rows.append(TokenSource(profile=profile, section=spec.section, source=source))
    stores = ", ".join(store.name for store in usable_stores()) or "none"
    _diag.info(f"token stores usable here: {stores}")
    emit(rows, fmt=fmt, columns=columns)


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
    if not pending:
        _diag.info("no plaintext tokens in the config; nothing to move")
        emit([], fmt=fmt, columns=columns)
        return
    chosen = pick_store(store)
    if chosen is None:
        raise ConfigError(
            no_store_message(pending[0][1], sections[pending[0][1]].env),
            category="unavailable",
        )
    if not dry_run:
        chosen.preflight()
    rows, failures = _move_all(pending, chosen, dry_run=dry_run)
    emit(rows, fmt=fmt, columns=columns)
    if failures:
        worst = most_severe(failures)
        raise ConfigError(
            f"{plural(len(failures), 'token')} could not be moved and stay in the config",
            category=worst.category,
            system=worst.system,
            hint=next((exc.hint for exc in failures if exc.hint), None),
        )
    if not dry_run:
        _diag.success(f"moved {plural(len(rows), 'token')} to {chosen.name}")


def _move_all(
    pending: Sequence[tuple[str, str, str]], store: TokenStore, *, dry_run: bool
) -> tuple[list[AuthOutcome], list[ConfigError]]:
    """Move each token; also the failures, each already a ``failed`` row."""
    repo = SettingsFileRepository()
    rows: list[AuthOutcome] = []
    failures: list[ConfigError] = []
    for profile, section, token in pending:
        moved = partial(AuthOutcome, section=section, profile=profile)
        try:
            entry = entry_name(profile, section)
            if dry_run:
                rows.append(moved(store=store.describe(entry), action="planned"))
                continue
            where = save_token(repo, section, profile, token, store)
        except ConfigError as exc:
            failures.append(exc)
            error = Failure.from_exception(exc)
            rows.append(moved(store=None, action="failed", detail=str(exc), error=error))
            continue
        rows.append(moved(store=where, action="moved"))
    return rows, failures


__all__ = [
    "Deleted",
    "build_root_auth_app",
    "delete_stored_token",
    "inherited_from_default",
    "plaintext_token",
    "refuse_inherited_token",
    "save_token",
]
