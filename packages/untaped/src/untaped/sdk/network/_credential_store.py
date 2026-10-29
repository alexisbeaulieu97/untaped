"""Where credentials are kept: an index in core's ``state.yml`` and this machine's password store.

The index is core's state rows ``credentials`` (:data:`INDEX`, which
``Core.credentials`` holds), in ``plugins/untaped/state.yml``: one row per
stored credential, holding its plugin, name, key fields, backend, and the
entry name or argv that reads it, never the secret. The file is written
0600, as every state file is. A row's id is
:func:`untaped.sdk.network.credentials.key_id`.

Secrets go to the stores :mod:`untaped.sdk.network._token_store` drives (macOS
``security``, ``secret-tool``, ``pass``) under service ``untaped``, entry
``<plugin>/<name>/<id>``, prefixed by a short hash of the config file's path
for a config other than ``~/.untaped/config.yml``, so two config files never
share an entry. Two rows never share one entry. A row may instead hold a
command (argv) that prints the secret when it is needed.

Only this module and :mod:`untaped.sdk.network._token_store` call a store's tools.
"""

from __future__ import annotations

import hashlib
import shlex
from collections.abc import Sequence
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from untaped.runtime.settings import resolve_config_path
from untaped.runtime.sink import Diagnostics
from untaped.sdk._home import CONFIG_FILE, CORE, DEFAULT_HOME
from untaped.sdk.errors import ConfigError
from untaped.sdk.fields import Key
from untaped.sdk.network._token_store import StoreName, TokenStore, no_backend_message, pick_store
from untaped.sdk.network.auth import check_program, read_command
from untaped.sdk.network.credentials import (
    SHORT_ID,
    BoundSecret,
    describe_key,
    key_data,
)
from untaped.sdk.storage.state import StateRows
from untaped.sdk.words import run_hint

if TYPE_CHECKING:
    from untaped.sdk.network.credentials import Credential, CredentialKey

Backend = Literal["security", "secret-tool", "pass", "command", "memory"]
"""How a row's secret is read: a store's tool, the row's own command, or
``untaped.sdk.testing``'s in-memory store (tests only)."""


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


_diag = Diagnostics()

_TEST_STORE: ContextVar[ReadableStore | None] = ContextVar("untaped_test_store", default=None)


def set_test_store(store: ReadableStore | None) -> Token[ReadableStore | None]:
    """Use ``store`` instead of this machine's (``untaped.sdk.testing``'s in-memory store)."""
    return _TEST_STORE.set(store)


def reset_test_store(token: Token[ReadableStore | None]) -> None:
    """Undo :func:`set_test_store`."""
    _TEST_STORE.reset(token)


class CredentialRow(BaseModel):
    """One stored credential in the index: what it is for and how to read it, never the secret."""

    model_config = ConfigDict(frozen=True)

    id: Annotated[str, Key] = Field(
        description="sha256 of the plugin, name and key: what the row answers."
    )
    plugin: str
    name: str
    key: dict[str, JsonValue] = Field(
        default_factory=dict, description="The key's fields that differ from their default."
    )
    backend: Backend
    entry: str | None = Field(default=None, description="The store entry, for a store backend.")
    argv: list[str] | None = Field(default=None, description="The command, for `command`.")
    label: str = Field(default="", description="How the store shows it: `awx token url=…`.")

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


#: Core's ``credentials``: the index's rows, read with no plugin class imported.
#: ``Core.credentials`` holds it, and ``auth forget`` removes its rows.
INDEX = StateRows[CredentialRow]()._bound_to(CORE, "credentials")


def rows() -> list[CredentialRow]:
    """Every stored row, in file order."""
    return INDEX.all()


def find_row(ident: str) -> CredentialRow | None:
    """The row filed under the full id ``ident``, or ``None``."""
    return INDEX.get(ident)


def ids_by_prefix(prefix: str) -> list[str]:
    """The ids starting with ``prefix`` (``auth forget --id``), a damaged row's too."""
    wanted = prefix.strip().lower()
    known = INDEX.keys()  # not the rows: a damaged one is found too
    return [ident for ident in known if ident.startswith(wanted)] if wanted else []


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


def store_available() -> bool:
    """Whether a token has a store to go to: this machine's, or a test's."""
    return _TEST_STORE.get() is not None or pick_store() is not None


def _memory_store() -> ReadableStore:
    store = _TEST_STORE.get()
    if store is None:
        raise ConfigError(
            "a stored credential names untaped.sdk.testing's in-memory store, which only tests use",
            hint=f"{run_hint('auth forget --id ID')}, then log in again",
        )
    return store


def _read_argv(row: CredentialRow) -> list[str]:
    store = row.store
    if isinstance(store, TokenStore) and row.entry is not None:
        return store.read_argv(row.entry)
    if not row.argv:
        raise ConfigError(
            f"the stored {row.plugin} {row.name} ({row.short}) names no command",
            hint=f"{run_hint(f'auth forget --id {row.short}')}, then log in again",
        )
    return list(row.argv)


def config_prefix() -> str:
    """The entry prefix for this home's config file: ``""`` for the default home's, else a hash.

    So two homes on one machine never read each other's entries.
    """
    path = resolve_config_path().expanduser().absolute()
    if path == (DEFAULT_HOME / CONFIG_FILE).expanduser().absolute():
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
    store: SecretStore | None = None,
) -> str:
    """Save ``token`` to a password store for ``key`` and file its row; where it went.

    The store is ``store`` (from :func:`ready_store`), else the named one,
    else the first usable one. The secret is written and read back before
    the row is filed, so a failure files nothing. An entry another row
    names is never reused.
    """
    if store is None:
        store = ready_store(credential, backend)
    ident = credential.id(key)
    entry = entry_for(credential, ident)
    taken = [row for row in rows() if row.entry == entry and row.id != ident]
    if taken:
        entry = f"{entry}-{ident[16:24]}"
    store.save(entry, token, label=label_for(credential, key))
    old = find_row(ident)
    INDEX.put(_row(credential, key, ident, backend=cast("Backend", store.name), entry=entry))
    if old is not None and (old.backend, old.entry) != (store.name, entry):
        try:
            _delete_entry(old)
        except ConfigError as exc:
            _diag.warning(f"stored, but the replaced entry {old.where()} stays: {exc}")
    return store.describe(entry)


def ready_store(credential: Credential[Any], backend: str | None = None) -> SecretStore:
    """The store a token for ``credential`` goes to, tested with a throwaway value.

    ``auth login`` calls it before asking for the token, so nobody pastes
    one only to learn it has nowhere to go.
    """
    store: SecretStore | None = _TEST_STORE.get() or pick_store(_store_name(backend))
    if store is None:
        message, hint = no_backend_message(credential.login)
        raise ConfigError(message, hint=hint, fix_by="user")
    store.preflight()
    return store


def store_command(credential: Credential[Any], key: CredentialKey, argv: Sequence[str]) -> str:
    """File a row reading the secret from ``argv`` when it is needed; how it reads."""
    command = [str(part) for part in argv]
    if not command or not command[0].strip():
        raise ConfigError("the token command is empty", category="usage")
    check_program(command, setting="--token-command", category="usage")
    ident = credential.id(key)
    old = find_row(ident)
    INDEX.put(_row(credential, key, ident, backend="command", argv=command))
    if old is not None:
        _delete_entry(old)
    return f"will run `{shlex.join(command)}` from the config folder when this token is needed"


def forget_row(ident: str) -> CredentialRow | None:
    """Delete the row ``ident`` and its store entry; the row, or ``None`` when absent."""
    row = find_row(ident)
    if row is None:
        return None
    _delete_entry(row)
    INDEX.delete(ident)
    return row


def _delete_entry(row: CredentialRow) -> None:
    """Delete ``row``'s store entry unless another row still names it."""
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


__all__ = [
    "INDEX",
    "Backend",
    "CredentialRow",
    "ReadableStore",
    "SecretStore",
    "config_prefix",
    "entry_for",
    "find_row",
    "forget_row",
    "ids_by_prefix",
    "label_for",
    "read_row",
    "reset_test_store",
    "rows",
    "set_test_store",
    "store_available",
    "store_command",
    "store_token",
]
