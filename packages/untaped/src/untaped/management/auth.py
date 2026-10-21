"""Root ``untaped auth …`` command group: keep API tokens out of ``config.yml``.

``auth set`` stores a secret with a tool on this machine
(:mod:`untaped.token_store`) and writes the command setting that reads it
back (``<section>.token_command`` for a ``token``); ``auth migrate`` does the
same for every secret stored in plain text; ``auth unset`` removes what
``auth set`` stored; ``auth status`` says where each one comes from without
running anything that reads it. Secrets are read from a hidden prompt or
stdin, never from an argument.

A plugin's secrets are its :class:`~untaped.setting_marks.Secret` fields (an
unmarked model's ``token`` with ``token_command``). ``<section>`` names the
one called ``token``, else the only one; ``<section>.<field>`` names any.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Any, Literal

from cyclopts import App, Parameter

from untaped.auth import auth_command, auth_target, token_override_env
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
from untaped.messages import command_line, hint, plural, run_hint
from untaped.output.diagnostic import Failure
from untaped.output.sink import Diagnostics
from untaped.plugins.registry import CompositionResult, settings_model
from untaped.profile_resolver import (
    DEFAULT_PROFILE,
    resolve_profiles,
    selected_profile,
)
from untaped.setting_marks import SetupMarks
from untaped.settings import active_settings_layout
from untaped.setup_status import notify_unmarked, plugin_marks
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
class _Secret:
    """One secret ``auth`` serves: a field of a composed section and its command setting."""

    section: str
    field: str
    command: str
    """Its command setting (``token_command`` for ``token``)."""
    target: str
    """What ``auth set`` takes for it: ``<section>``, or ``<section>.<field>``."""
    env: tuple[str, ...]
    """Its conventional environment variables, in order."""
    marks: SetupMarks | None = None
    """What its plugin marks; legacy ones warn where ``auth`` reads them."""

    @property
    def key(self) -> str:
        """``<section>.<field>``."""
        return f"{self.section}.{self.field}"

    @property
    def label(self) -> str:
        """How messages name it: ``the awx token``, ``the acme.webhook_secret secret``."""
        if self.field == "token":
            return f"the {self.section} token"
        return f"the {self.key} secret"

    def entry(self, profile: str) -> str:
        """Its store entry in ``profile``."""
        return entry_name(profile, self.section, field=self.field, command=self.command)

    def notify(self) -> None:
        """Warn once when the section is read through the deprecated unmarked fallback."""
        notify_unmarked(self.section, self.marks)


def _secrets(result: CompositionResult) -> list[_Secret]:
    """Every secret ``auth`` can store: each marked ``Secret``, or an unmarked ``token``.

    An unmarked model's ``token`` counts only beside its ``token_command``.
    """
    found: list[_Secret] = []
    for registered in result.plugins:
        spec = registered.spec
        marks = plugin_marks(spec)
        if marks is None:
            continue
        model = settings_model(spec)
        for field in marks.secrets:
            command = auth_command(model, field.key)
            target = auth_target(model, spec.name, field.key)
            if command is None or target is None or field.secret is None:
                continue
            env = field.secret.env
            found.append(_Secret(spec.name, field.key, command, target, env, marks))
    return found


def save_token(
    repo: SettingsFileRepository,
    section: str,
    profile: str,
    token: str,
    store: TokenStore,
    *,
    field: str = "token",
    command: str = "token_command",
) -> str:
    """Store the secret ``field`` of ``section`` in ``profile`` and point the config at it.

    Validates the config write first, stores and reads the secret back, then
    writes its ``command`` setting and removes the profile's plaintext
    ``<section>.<field>``. Returns the entry's description. Nothing in the
    config changes when storing fails.
    """
    entry = entry_name(profile, section, field=field, command=command)
    refuse_inherited_token(section, profile, field=field, command=command)
    argv = json.dumps(store.read_argv(entry))
    repo.set_value(f"{section}.{command}", argv, profile=profile, dry_run=True)
    store.save(entry, token)
    repo.set_value(f"{section}.{command}", argv, profile=profile)
    repo.unset_value(f"{section}.{field}", profile=profile)
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


def refuse_inherited_token(
    section: str, profile: str, *, field: str = "token", command: str = "token_command"
) -> None:
    """Raise when ``default``'s plaintext ``field`` would win over ``profile``'s command."""
    if inherited_from_default(section, profile, field):
        raise ConfigError(
            f"{section}.{field} is set in profile {DEFAULT_PROFILE} and would override "
            f"{section}.{command} in profile {profile}; move it out of {DEFAULT_PROFILE} "
            f"first\n{hint('auth migrate')}"
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


def plaintext_token(data: dict[str, Any] | None, section: str, field: str = "token") -> str | None:
    """The plaintext ``<section>.<field>`` in one profile's own ``data``, if any."""
    node = (data or {}).get(section)
    token = node.get(field) if isinstance(node, dict) else None
    return token.strip() if isinstance(token, str) and token.strip() else None


def build_root_auth_app(*, result: CompositionResult) -> App:
    """Return the root ``auth`` command group for one composition."""
    app = create_app(name="auth", help="Keep API tokens out of ``config.yml``.")

    @app.command(name="set")
    @writes
    def set_command(
        section: Annotated[
            str,
            Parameter(help="Section whose token to store, e.g. awx, or one secret: acme.hook."),
        ],
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
        Running it again replaces the stored token. ``SECTION`` is the
        section's ``token``, else its only secret; ``SECTION.FIELD`` names
        one of several (its setting is ``<section>.<field>_command``).
        """
        with report_errors():
            _set(result, section, stdin=stdin, store=store, fmt=fmt, columns=columns)

    @app.command(name="unset")
    @writes(destructive=True)
    def unset_command(
        section: Annotated[
            str, Parameter(help="Section whose stored token to remove, or one secret: acme.hook.")
        ],
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
        untaped did not write is left alone too. ``SECTION`` and
        ``SECTION.FIELD`` name a secret as ``auth set`` does.
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


def _section(result: CompositionResult, name: str, *, verb: str) -> _Secret:
    """The secret ``name`` names: ``<section>`` (its default secret) or ``<section>.<field>``.

    A section with several secrets and none named ``token`` refuses a bare
    name, naming each; ``verb`` is the command that refuses (``set``).
    """
    secrets = _secrets(result)
    found = next((each for each in secrets if name in (each.target, each.key)), None)
    if found is None:
        several = [each.key for each in secrets if each.section == name]
        if several:
            names = ", ".join(several)
            raise ConfigError(
                f"{name} has several secrets ({names}); name one, as in "
                f"`{command_line(f'auth {verb} {several[0]}')}`",
                category="invalid",
            )
        known = ", ".join(sorted({each.target for each in secrets})) or "(none)"
        raise ConfigError(f"unknown token section {name!r}; known: {known}", category="invalid")
    found.notify()
    return found


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
    secret = _section(result, section, verb="set")
    profile = _target_profile()
    secret.entry(profile)
    refuse_inherited_token(secret.section, profile, field=secret.field, command=secret.command)
    chosen = pick_store(store)
    if chosen is None:
        message = no_store_message(secret.section, secret.env, command=secret.command)
        raise ConfigError(message, category="unavailable")
    chosen.preflight()
    ui = ui_context(strict=False)
    token = _read_stdin_token() if stdin else _prompt_token(ui, secret)
    repo = SettingsFileRepository()
    where = save_token(
        repo, secret.section, profile, token, chosen, field=secret.field, command=secret.command
    )
    _diag.success(
        f"stored {secret.label} in {where}; {secret.section}.{secret.command} set in "
        f"profile {profile}"
    )
    _warn_env_override(secret)
    emit(_outcome(secret, profile, store=where, action="stored"), fmt=fmt, columns=columns)


def _outcome(secret: _Secret, profile: str, **values: Any) -> AuthOutcome:
    return AuthOutcome(section=secret.section, field=secret.field, profile=profile, **values)


def _read_stdin_token() -> str:
    token = read_stdin_text().strip()
    if not token:
        raise ConfigError("no token received on stdin", category="invalid")
    if "\n" in token or "\r" in token:
        raise ConfigError("--stdin expects the token on one line", category="invalid")
    return token


def _prompt_token(ui: UiContext, secret: _Secret) -> str:
    with ui.terminal(refusal="no terminal to prompt for the token; pipe it with --stdin"):
        return ui.secret(secret.label.removeprefix("the ")).strip()


def _warn_env_override(secret: _Secret) -> None:
    override = token_override_env(secret.section, field=secret.field)
    if override is not None:
        _diag.warning(
            f"${override} is set and wins over the stored {_noun(secret)} until you unset it",
        )


def _noun(secret: _Secret) -> str:
    return "token" if secret.field == "token" else "secret"


def _unset(
    result: CompositionResult,
    section: str,
    *,
    yes: bool,
    dry_run: bool,
    fmt: Any,
    columns: list[str] | None,
) -> None:
    secret = _section(result, section, verb="unset")
    name, setting = secret.section, f"{secret.section}.{secret.command}"
    profile = _target_profile()
    raw = read_config_dict()
    own = (active_settings_layout().profile_data(raw, profile) or {}).get(name)
    argv = own.get(secret.command) if isinstance(own, dict) else None
    ui = ui_context(strict=False)
    row = _outcome(secret, profile, store=None, action="unchanged")
    if not argv:
        _, provenance = resolve_profiles(raw, active_override=profile)
        holder = provenance.get((name, secret.command))
        if holder is not None and holder != profile:
            _diag.info(
                f"{setting} is inherited from profile {holder}; profile {profile} is unchanged"
            )
            _diag.hint(run_hint(f"--profile {holder} auth unset {secret.target}"))
        else:
            _diag.info(f"{setting} is not set in profile {profile}")
        emit(row, fmt=fmt, columns=columns)
        return
    preset = preset_entry(argv)
    if preset is None:
        raise ConfigError(
            f"{setting} in profile {profile} was not written by untaped; "
            f"remove it yourself\n{hint(f'config unset {setting}')}",
            category="invalid",
        )
    where = preset[0].describe(preset[1])
    row = row.model_copy(update={"store": where})
    if dry_run:
        emit(row.model_copy(update={"action": "planned"}), fmt=fmt, columns=columns)
        return
    ui.confirm_or_cancel(
        f"Delete {secret.label} from {where} and unset {setting} in profile {profile}?",
        assume_yes=yes,
    )
    deleted, _ = delete_stored_token(argv)
    SettingsFileRepository().unset_value(setting, profile=profile)
    if deleted == "gone":
        _diag.success(
            f"{secret.label} was already gone from {where}; unset {setting} (profile {profile})"
        )
    else:
        _diag.success(f"deleted {secret.label} from {where} (profile {profile})")
    emit(row.model_copy(update={"action": deleted}), fmt=fmt, columns=columns)


def _status(result: CompositionResult, *, fmt: Any, columns: list[str] | None) -> None:
    raw = read_config_dict()
    layout = active_settings_layout()
    profiles = layout.profile_names(raw) or [DEFAULT_PROFILE]
    secrets = _secrets(result)
    for each in secrets:
        each.notify()
    rows: list[TokenSource] = []
    for profile in profiles:
        effective, provenance = resolve_profiles(raw, active_override=profile)
        for secret in secrets:
            node = effective.get(secret.section)
            source, key = _source(secret, node if isinstance(node, dict) else {})
            # Other profiles inherit `default`; list them only for a secret of their own
            # (one that default's plaintext secret shadows drops out too).
            if profile != DEFAULT_PROFILE and (
                key is None or provenance.get((secret.section, key)) != profile
            ):
                continue
            rows.append(
                TokenSource(
                    profile=profile, section=secret.section, field=secret.field, source=source
                )
            )
    stores = ", ".join(store.name for store in usable_stores()) or "none"
    _diag.info(f"token stores usable here: {stores}")
    emit(rows, fmt=fmt, columns=columns)


def _source(secret: _Secret, node: dict[str, Any]) -> tuple[str, str | None]:
    """Name a secret's source and the config key that sets it, if any."""
    override = token_override_env(secret.section, field=secret.field)
    if override is not None:
        return f"${override}", None
    token = node.get(secret.field)
    if isinstance(token, str) and token.strip():
        return "config.yml (plain text)", secret.field
    argv = node.get(secret.command)
    if isinstance(argv, list) and argv:
        preset = preset_entry([str(part) for part in argv])
        return (preset[0].describe(preset[1]) if preset else secret.command), secret.command
    env = next((name for name in secret.env if os.environ.get(name, "").strip()), None)
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
    secrets = _secrets(result)
    for each in secrets:
        each.notify()
    pending = [
        (profile, secret, token)
        for profile in ordered
        for secret in secrets
        if (
            token := plaintext_token(
                layout.profile_data(raw, profile), secret.section, secret.field
            )
        )
        is not None
    ]
    if not pending:
        _diag.info("no plaintext tokens in the config; nothing to move")
        emit([], fmt=fmt, columns=columns)
        return
    chosen = pick_store(store)
    if chosen is None:
        first = pending[0][1]
        raise ConfigError(
            no_store_message(first.section, first.env, command=first.command),
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
    pending: Sequence[tuple[str, _Secret, str]], store: TokenStore, *, dry_run: bool
) -> tuple[list[AuthOutcome], list[ConfigError]]:
    """Move each secret; also the failures, each already a ``failed`` row."""
    repo = SettingsFileRepository()
    rows: list[AuthOutcome] = []
    failures: list[ConfigError] = []
    for profile, secret, token in pending:
        moved = partial(_outcome, secret, profile)
        try:
            entry = secret.entry(profile)
            if dry_run:
                rows.append(moved(store=store.describe(entry), action="planned"))
                continue
            where = save_token(
                repo,
                secret.section,
                profile,
                token,
                store,
                field=secret.field,
                command=secret.command,
            )
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
