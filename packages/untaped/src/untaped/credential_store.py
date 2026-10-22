"""Where credentials are kept: an index in ``state.yml`` and this machine's password store.

The index is core's rows in the reserved state section ``credentials``
(:func:`untaped.state.core_rows`): one row per stored credential, holding
its plugin, name, key fields, backend, and the entry name or argv that
reads it, never the secret. The file is written 0600 like the rest of
``state.yml``. A row's id is :func:`untaped.credentials.key_id`.

Secrets go to the stores :mod:`untaped.token_store` drives (macOS
``security``, ``secret-tool``, ``pass``) under service ``untaped``, entry
``<plugin>/<name>/<id>``, prefixed by a short hash of the config file's path
for a config other than ``~/.untaped/config.yml``, so two config files never
share an entry. Two rows never share one entry. A row may instead hold a
command (argv) that prints the secret when it is needed.

Only this module and :mod:`untaped.token_store` call a store's tools.
"""

from __future__ import annotations

import hashlib
import shlex
from collections.abc import Callable, Sequence
from contextvars import ContextVar, Token
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

from untaped.auth import check_program, read_command, token_override_env
from untaped.credentials import (
    SHORT_ID,
    BoundSecret,
    describe_key,
    key_data,
)
from untaped.errors import ConfigError
from untaped.settings import DEFAULT_CONFIG_PATH, resolve_config_path
from untaped.token_store import StoreName, TokenStore, no_backend_message, pick_store

if TYPE_CHECKING:
    from untaped.credentials import Credential, CredentialKey
    from untaped.state import StateRows

Backend = Literal["security", "secret-tool", "pass", "command", "memory"]
"""How a row's secret is read: a store's tool, the row's own command, or
``untaped.testing``'s in-memory store (tests only)."""


class SecretStore(Protocol):
    """A store secrets are written to and read from: a :class:`TokenStore`, or a test's own."""

    @property
    def name(self) -> str: ...

    def preflight(self) -> None: ...

    def save(self, entry: str, token: str, *, label: str | None = None) -> None: ...

    def delete(self, entry: str) -> None: ...

    def gone(self, entry: str) -> bool: ...

    def describe(self, entry: str) -> str: ...


class ReadableStore(SecretStore, Protocol):
    """A store that reads an entry itself, rather than through a command (a test's)."""

    def read(self, entry: str) -> str: ...


_TEST_STORE: ContextVar[ReadableStore | None] = ContextVar("untaped_test_store", default=None)


def set_test_store(store: ReadableStore | None) -> Token[ReadableStore | None]:
    """Use ``store`` instead of this machine's (``untaped.testing``'s in-memory store)."""
    return _TEST_STORE.set(store)


def reset_test_store(token: Token[ReadableStore | None]) -> None:
    """Undo :func:`set_test_store`."""
    _TEST_STORE.reset(token)


SECTION = "credentials"
"""The reserved state section the index lives in."""


class CredentialRow(BaseModel):
    """One stored credential in the index: what it is for and how to read it, never the secret."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="sha256 of the plugin, name and key: what the row answers.")
    plugin: str
    name: str
    key: dict[str, JsonValue] = Field(
        default_factory=dict, description="The key's fields that differ from their default."
    )
    backend: Backend
    entry: str | None = Field(default=None, description="The store entry, for a store backend.")
    argv: list[str] | None = Field(default=None, description="The command, for `command`.")
    label: str = Field(default="", description="How the store shows it: `awx token url=…`.")
    migrated: bool = Field(
        default=False,
        description="Its entry is an 11.0 one (`<profile>/<section>`) that older untaped reads.",
    )

    @property
    def short(self) -> str:
        """The id as ``auth list`` shows it."""
        return self.id[:SHORT_ID]

    @property
    def store(self) -> SecretStore | None:
        """The store a store-backed row reads from."""
        if self.backend == "command":
            return None
        if self.backend == "memory":
            return _memory_store()
        return TokenStore(self.backend)

    def where(self) -> str:
        """Where the secret lives, for messages: the store's entry, or ``command: <program> …``."""
        store = self.store
        if store is not None and self.entry is not None:
            return store.describe(self.entry)
        program = (self.argv or ["?"])[0]
        return f"command: {program}{' …' if len(self.argv or []) > 1 else ''}"


@cache
def index() -> StateRows[CredentialRow]:
    """The credential index's rows (core's, under ``credentials.index`` in ``state.yml``)."""
    from untaped.state import core_rows  # noqa: PLC0415 - state imports the config file

    return core_rows(
        SECTION,
        "index",
        model=CredentialRow,
        key_field="id",
        remove_hint="untaped auth forget --id {key}",
    )


def rows() -> list[CredentialRow]:
    """Every stored row, in file order."""
    return index().all()


def find_row(ident: str) -> CredentialRow | None:
    """The row filed under the full id ``ident``, or ``None``."""
    return index().get(ident)


def rows_by_prefix(prefix: str) -> list[CredentialRow]:
    """The rows whose id starts with ``prefix`` (``auth forget --id``)."""
    wanted = prefix.strip().lower()
    return [row for row in rows() if row.id.startswith(wanted)] if wanted else []


def read_row(row: CredentialRow, *, origin: str | None) -> BoundSecret:
    """The secret ``row`` points at, read on first use, bound to ``origin``."""
    store, entry = row.store, row.entry
    source = f"stored {row.short}"
    if isinstance(store, TokenStore) or store is None or entry is None:
        argv = _read_argv(row)
        setting = f"the stored {row.plugin} {row.name} ({row.short})"
        return BoundSecret(
            lambda: read_command(argv, setting=setting), origin=origin, source=source
        )
    readable = cast("ReadableStore", store)
    return BoundSecret(lambda: readable.read(entry), origin=origin, source=source)


def _memory_store() -> ReadableStore:
    store = _TEST_STORE.get()
    if store is None:
        raise ConfigError(
            "a stored credential names untaped.testing's in-memory store, which only tests use",
            hint="untaped auth forget --id <id>, then log in again",
        )
    return store


def _read_argv(row: CredentialRow) -> list[str]:
    store = row.store
    if isinstance(store, TokenStore) and row.entry is not None:
        return store.read_argv(row.entry)
    if not row.argv:
        raise ConfigError(
            f"the stored {row.plugin} {row.name} ({row.short}) names no command",
            hint=f"untaped auth forget --id {row.short}, then log in again",
        )
    return list(row.argv)


def config_prefix() -> str:
    """The entry prefix for this config file: ``""`` for the default one, else a short hash.

    So two config files on one machine never read each other's entries.
    """
    path = resolve_config_path().expanduser().absolute()
    if path == Path(DEFAULT_CONFIG_PATH).expanduser().absolute():
        return ""
    return hashlib.sha256(str(path).encode()).hexdigest()[:8] + "/"


def entry_for(credential: Credential[Any], ident: str) -> str:
    """The store entry for key id ``ident``: ``[<cfg>/]<plugin>/<name>/<id>``."""
    return f"{config_prefix()}{credential.plugin}/{credential.name}/{ident[:16]}"


def label_for(credential: Credential[Any], key: CredentialKey) -> str:
    """``awx token url=https://…``: how a store lists the entry."""
    return f"{credential.label} {describe_key(key)}"


def _row(
    credential: Credential[Any], key: CredentialKey, ident: str, **values: Any
) -> CredentialRow:
    return CredentialRow(
        id=ident,
        plugin=credential.plugin,
        name=credential.name,
        key=key_data(key),
        label=label_for(credential, key),
        **values,
    )


def store_token(
    credential: Credential[Any],
    key: CredentialKey,
    token: str,
    *,
    backend: str | None = None,
) -> str:
    """Save ``token`` to a password store for ``key`` and file its row; where it went.

    The store is the named one, else the first usable one. The secret is
    written and read back before the row is filed, so a failure files
    nothing. An entry another row names is never reused.
    """
    store: SecretStore | None = _TEST_STORE.get() or pick_store(_store_name(backend))
    if store is None:
        message, hint = no_backend_message(credential.plugin, credential.name)
        raise ConfigError(message, hint=hint, fix_by="user")
    store.preflight()
    ident = credential.id(key)
    entry = entry_for(credential, ident)
    taken = [row for row in rows() if row.entry == entry and row.id != ident]
    if taken:
        entry = f"{entry}-{ident[16:24]}"
    store.save(entry, token, label=label_for(credential, key))
    index().put(_row(credential, key, ident, backend=cast("Backend", store.name), entry=entry))
    return store.describe(entry)


def store_command(credential: Credential[Any], key: CredentialKey, argv: Sequence[str]) -> str:
    """File a row reading the secret from ``argv`` when it is needed; how it reads."""
    command = [str(part) for part in argv]
    if not command or not command[0].strip():
        raise ConfigError("the token command is empty", category="usage")
    check_program(command, setting="--token-command", category="usage")
    ident = credential.id(key)
    old = find_row(ident)
    index().put(_row(credential, key, ident, backend="command", argv=command))
    if old is not None and old.store is not None and old.entry is not None:
        _delete_entry(old)
    return f"will run `{shlex.join(command)}` from the config folder when this token is needed"


def forget_row(ident: str) -> CredentialRow | None:
    """Delete the row ``ident`` and its store entry; the row, or ``None`` when absent.

    A migrated row's entry is left: an older untaped still reads it.
    """
    row = find_row(ident)
    if row is None:
        return None
    if not row.migrated:
        _delete_entry(row)
    index().delete(ident)
    return row


def _delete_entry(row: CredentialRow) -> None:
    """Delete ``row``'s store entry unless another row still names it (a migrated one)."""
    store = row.store
    if store is None or row.entry is None:
        return
    if any(other.entry == row.entry and other.id != row.id for other in rows()):
        return
    try:
        store.delete(row.entry)
    except ConfigError:
        if not store.gone(row.entry):
            raise


def _store_name(backend: str | None) -> StoreName | None:
    if backend is None:
        return None
    if backend not in ("security", "secret-tool", "pass"):
        raise ConfigError(
            f"unknown store {backend!r}; known: security, secret-tool, pass", category="usage"
        )
    return backend  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# The deprecated config settings (11.x only)
# --------------------------------------------------------------------------- #


def legacy_values(
    credential: Credential[Any],
) -> tuple[str, str | Callable[[], str]] | None:
    """``(setting, value)`` from the old ``<plugin>.<name>`` or ``<plugin>.<name>_command``.

    Both settings are deprecated, so reading one from the config file warns
    once (as any deprecated setting does). The override variable is step 1
    of ``get``, so it is not read here.
    """
    from untaped.settings import env_var_setting, load_settings_section  # noqa: PLC0415

    plugin, name = credential.plugin, credential.name
    try:
        section = load_settings_section(plugin)
    except ConfigError:
        return None
    if section is None:
        return None
    value = getattr(section, name, None)
    if (
        isinstance(value, SecretStr)
        and value.get_secret_value().strip()
        and token_override_env(plugin, field=name) is None
    ):
        return f"{plugin}.{name}", value.get_secret_value().strip()
    command_field = f"{name}_command"
    argv = getattr(section, command_field, None)
    if argv:
        setting = env_var_setting((plugin, command_field)) or f"{plugin}.{command_field}"
        command = [str(part) for part in argv]
        return setting, lambda: read_command(command, setting=setting)
    return None


__all__ = [
    "SECTION",
    "Backend",
    "CredentialRow",
    "ReadableStore",
    "SecretStore",
    "config_prefix",
    "entry_for",
    "find_row",
    "forget_row",
    "index",
    "label_for",
    "legacy_values",
    "read_row",
    "reset_test_store",
    "rows",
    "rows_by_prefix",
    "set_test_store",
    "store_command",
    "store_token",
]
