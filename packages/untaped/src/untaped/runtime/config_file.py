"""Read/write helpers for ``~/.untaped/config.yml`` and a plugin's ``state.yml``.

These are the lowest-level primitives behind the root ``untaped config
set/unset`` commands and plugin state writes. They never validate
against the Settings schema — that's the caller's job. Settings writes only
touch the config file; state writes only touch a plugin's state file.

Writes are round-trips (see :mod:`untaped.runtime.yaml_roundtrip`): only the keys a
mutation changed are rewritten, so the user's comments, key order and
formatting survive ``config set``, profile, and state writes.
"""

from __future__ import annotations

import contextlib
import copy
import math
import os
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from untaped.runtime.settings import (
    FORMAT_VERSION,
    FormatVersionError,
    get_settings,
    load_config_yaml,
    load_state_yaml,
    resolve_config_path,
)
from untaped.runtime.yaml_roundtrip import KeyRename, plain_dump, render_preserving
from untaped.sdk.errors import ConfigError
from untaped.sdk.storage._fs import atomic_write, locked_write

_DEFAULT_LOCK_TIMEOUT = 5.0


def read_config_dict(path: Path | None = None) -> dict[str, Any]:
    """Load the user's config file as a plain dict.

    Returns an empty dict if the file does not exist or is empty.
    Translates YAML syntax errors, read failures (e.g. permissions), and a
    non-mapping document root into :class:`ConfigError` so they surface via
    core's error boundary instead of a traceback.
    """
    return load_config_yaml(path or resolve_config_path())


def read_state_dict(path: Path) -> dict[str, Any]:
    """Load a plugin's state file as a plain dict, raising as :func:`read_config_dict` does."""
    return load_state_yaml(path)


def write_config_dict(
    data: dict[str, Any], path: Path | None = None, *, renames: Iterable[KeyRename] = ()
) -> None:
    """Atomically write ``data`` back to the config file.

    Only keys that differ from the file's current content are rewritten;
    comments, key order and formatting of everything else are preserved.
    ``renames`` names keys that moved (``(old_path, new_path)``); a key renamed
    within its mapping keeps its position and comment.
    A ``data`` without ``format_version`` is written with
    :data:`~untaped.runtime.settings.FORMAT_VERSION` first.
    Creates parent directories if needed. The write goes through
    :func:`~untaped.sdk.storage._fs.atomic_write` with permissions ``0o600`` (so secrets
    are never world-readable, even briefly): it is durable, writes through a
    symlinked config file, and a failed write leaves the original untouched
    and no temp file behind.
    """
    target = path or resolve_config_path()
    if "format_version" not in data:
        data = {"format_version": FORMAT_VERSION, **data}
    atomic_write(target, _render(data, target, load_config_yaml, tuple(renames)), mode=0o600)


def write_state_dict(data: dict[str, Any], path: Path) -> None:
    """Atomically write a state file, as :func:`write_config_dict` writes ``config.yml``."""
    atomic_write(path, _render(data, path, load_state_yaml), mode=0o600)


def _render(
    data: dict[str, Any],
    target: Path,
    load: Callable[[Path], dict[str, Any]],
    renames: tuple[KeyRename, ...] = (),
) -> str:
    try:
        original = target.read_text(encoding="utf-8")
        before = load(target)
    except FormatVersionError:
        raise
    except OSError, UnicodeDecodeError, ConfigError:
        return plain_dump(data)
    return render_preserving(original, before, data, renames=renames)


def mutate_config(
    fn: Callable[[dict[str, Any]], None],
    path: Path | None = None,
    *,
    renames: Sequence[KeyRename] = (),
) -> None:
    """Read, mutate, and write the config file under an advisory lock.

    Two concurrent CLI invocations both read-modify-writing the YAML can
    silently drop one of the writes. ``mutate_config`` serialises the
    load-mutate-store sequence behind a per-file lock so the second caller
    sees the first caller's commit, never an older snapshot.

    The callback receives a mutable dict; mutate it in place. A callback that
    renames keys appends them to the ``renames`` list it was given, read
    after it returns (see :func:`write_config_dict`). The atomic
    write only runs after the callback returns successfully — exceptions
    leave the on-disk file untouched. The dict is also snapshot before
    the callback runs and the write is skipped when nothing changed, so
    no-ops (deleting a missing profile, unsetting a missing key) don't
    spuriously create or reformat the YAML file. ``get_settings``'s cache
    is cleared after a successful write so the rest of the process sees
    the new values without callers needing to do it themselves. A write to
    the same file from inside ``fn`` raises :class:`UntapedError` and writes
    nothing.

    Override the lock acquisition timeout via ``UNTAPED_CONFIG_LOCK_TIMEOUT``
    (seconds, non-negative float; anything else raises ``ConfigError``).
    Default is 5 seconds.
    """
    target = path or resolve_config_path()
    with _locked(target):
        data = read_config_dict(target)
        before = copy.deepcopy(data)
        fn(data)
        if data != before:
            write_config_dict(data, target, renames=renames)
            get_settings.cache_clear()


@contextlib.contextmanager
def _locked(target: Path) -> Iterator[None]:
    """Hold the config lock of ``target``: :func:`locked_write` with the config's wait."""
    timeout = _lock_timeout()
    busy = (
        f"could not acquire lock on {target}; another untaped process is "
        f"writing to it (waited {timeout}s)."
    )
    with locked_write(target, timeout=timeout, busy=busy):
        yield


def _lock_timeout() -> float:
    raw = os.environ.get("UNTAPED_CONFIG_LOCK_TIMEOUT", "").strip()
    if not raw:
        return _DEFAULT_LOCK_TIMEOUT
    try:
        timeout = float(raw)
    except ValueError:
        timeout = math.nan
    if not math.isfinite(timeout) or timeout < 0:
        raise ConfigError(
            f"invalid UNTAPED_CONFIG_LOCK_TIMEOUT {raw!r}: expected a non-negative "
            "number of seconds"
        )
    return timeout


def read_config_text(path: Path | None = None) -> str | None:
    """Return the config file's text verbatim (newlines untranslated), or ``None`` if absent."""
    target = path or resolve_config_path()
    try:
        with target.open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"could not read {target}: {exc}") from exc


def replace_config_text(text: str, *, expected: str | None, path: Path | None = None) -> None:
    """Save ``text`` verbatim as the config file; the caller validated it.

    Runs under the config lock. ``expected`` is the content the caller
    started from (``None``: no file); if the file changed since, nothing is
    written and :class:`ConfigError` is raised so another write is never
    silently lost. The text is written like every other config write
    (atomically, owner-only, through a symlink); a failed write raises
    :class:`ConfigError` and leaves the file as it was.
    """
    target = path or resolve_config_path()
    with _locked(target):
        if read_config_text(target) != expected:
            raise ConfigError(f"{target} changed while it was being edited", category="conflict")
        try:
            atomic_write(target, text, mode=0o600)
        except OSError as exc:
            raise ConfigError(f"could not write {target}: {exc.strerror or exc}") from exc
        get_settings.cache_clear()


def set_at_path(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """Set ``data[path] = value`` in place, creating intermediate dicts."""
    cur = data
    for key in path[:-1]:
        existing = cur.get(key)
        if not isinstance(existing, dict):
            cur[key] = {}
        cur = cur[key]
    cur[path[-1]] = _to_yaml_value(value)


def unset_at_path(data: dict[str, Any], path: tuple[str, ...]) -> bool:
    """Remove ``path`` from ``data``, cleaning up empty parents.

    Returns ``True`` if something was removed.
    """
    chain: list[tuple[dict[str, Any], str]] = []
    cur: Any = data
    for key in path[:-1]:
        if not isinstance(cur, dict) or key not in cur:
            return False
        chain.append((cur, key))
        cur = cur[key]
    last = path[-1]
    if not isinstance(cur, dict) or last not in cur:
        return False
    del cur[last]
    for parent, key in reversed(chain):
        if isinstance(parent[key], dict) and not parent[key]:
            del parent[key]
    return True


def _to_yaml_value(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, Path):
        return str(value)
    return value
