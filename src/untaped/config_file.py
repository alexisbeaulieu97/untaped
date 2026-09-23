"""Read/write helpers for ``~/.untaped/config.yml`` and ``state.yml``.

These are the lowest-level primitives behind the root ``untaped config
set/unset`` commands and capability state writes. They never validate
against the Settings schema — that's the caller's job. Settings writes only
touch the config file; state writes only touch the state file, except the
one-time move of a legacy state section out of ``config.yml``.

Writes are round-trips (see :mod:`untaped.yaml_roundtrip`): only the keys a
mutation changed are rewritten, so the user's comments, key order and
formatting survive ``config set``, profile, and state writes.
"""

from __future__ import annotations

import contextlib
import copy
import math
import os
import sys
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout
from pydantic import SecretStr

from untaped.errors import ConfigError
from untaped.settings import (
    check_state_section_name,
    get_settings,
    load_config_yaml,
    resolve_config_path,
    resolve_state_path,
    state_section_source,
)
from untaped.yaml_roundtrip import plain_dump, render_preserving

_MISSING = object()
# Typed as ``Any`` so ``value is MISSING`` at call sites doesn't
# narrow the result to ``object`` under mypy strict.
MISSING: Any = _MISSING

_DEFAULT_LOCK_TIMEOUT = 5.0


def read_config_dict(path: Path | None = None) -> dict[str, Any]:
    """Load the user's config file as a plain dict.

    Returns an empty dict if the file does not exist or is empty.
    Translates YAML syntax errors, read failures (e.g. permissions), and a
    non-mapping document root into :class:`ConfigError` so they surface via
    ``report_errors`` instead of a traceback.
    """
    return load_config_yaml(path or resolve_config_path())


def write_config_dict(data: dict[str, Any], path: Path | None = None) -> None:
    """Atomically write ``data`` back to the config file.

    Only keys that differ from the file's current content are rewritten;
    comments, key order and formatting of everything else are preserved.
    Creates parent directories if needed. The data is written to a unique
    temp file created with permissions ``0o600`` (so secrets are never
    world-readable, even briefly) and atomically renamed over the target;
    a failed write leaves the original untouched and no temp file behind.
    """
    target = path or resolve_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = _render(data, target)
    # A unique temp file created 0600 from the start (O_EXCL, never
    # world-readable, even briefly) in the target's directory so the final
    # ``os.replace`` is atomic; removed again if anything fails.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _render(data: dict[str, Any], target: Path) -> str:
    try:
        original = target.read_text(encoding="utf-8")
        before = load_config_yaml(target)
    except OSError, UnicodeDecodeError, ConfigError:
        return plain_dump(data)
    return render_preserving(original, before, data)


def mutate_config(fn: Callable[[dict[str, Any]], None], path: Path | None = None) -> None:
    """Read, mutate, and write the config file under an advisory lock.

    Two concurrent CLI invocations both read-modify-writing the YAML can
    silently drop one of the writes. ``mutate_config`` serialises the
    load-mutate-store sequence behind a per-file lock so the second caller
    sees the first caller's commit, never an older snapshot.

    The callback receives a mutable dict; mutate it in place. The atomic
    write only runs after the callback returns successfully — exceptions
    leave the on-disk file untouched. The dict is also snapshot before
    the callback runs and the write is skipped when nothing changed, so
    no-ops (deleting a missing profile, unsetting a missing key) don't
    spuriously create or reformat the YAML file. ``get_settings``'s cache
    is cleared after a successful write so the rest of the process sees
    the new values without callers needing to do it themselves.

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
            write_config_dict(data, target)
            get_settings.cache_clear()


@contextlib.contextmanager
def _locked(target: Path) -> Iterator[None]:
    """Hold the advisory ``<target>.lock`` (creating the parent directory)."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"could not create the directory for {target}: {exc.strerror or exc}"
        ) from exc
    timeout = _lock_timeout()
    lock = FileLock(str(target) + ".lock", timeout=timeout)
    try:
        lock.acquire()
    except Timeout as exc:
        raise ConfigError(
            f"could not acquire lock on {target}; another untaped process is "
            f"writing to it (waited {timeout}s)."
        ) from exc
    except OSError as exc:
        raise ConfigError(f"could not lock {target}: {exc.strerror or exc}") from exc
    try:
        yield
    finally:
        lock.release()


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


def ensure_config(path: Path | None = None) -> Path:
    """Create an empty config file (and its parent dir) if absent. Idempotent.

    Returns the resolved config path. An existing file is never touched.
    """
    target = path or resolve_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text("", encoding="utf-8")
        os.chmod(target, 0o600)
    return target


def read_tool_state(
    section: str, path: Path | None = None, *, config_path: Path | None = None
) -> dict[str, Any]:
    """Return a copy of a tool's state ``section`` dict, or ``{}``.

    State is read from ``state.yml`` (``path``, default
    :func:`~untaped.settings.resolve_state_path`). When that file lacks the
    section, a legacy copy at the top level of the config file is returned
    with a once-per-process deprecation warning; ``config_path`` names that
    file (default: the resolved config file when ``path`` is also default,
    otherwise no legacy lookup).
    """
    check_state_section_name(section)
    state_path = path or resolve_state_path()
    legacy_path = _legacy_path(path, config_path)
    state_raw = read_config_dict(state_path)
    config_raw = read_config_dict(legacy_path) if legacy_path and section not in state_raw else {}
    found = state_section_source(
        section,
        state_raw,
        config_raw,
        state_path=state_path,
        config_path=legacy_path or state_path,
    )
    raw = found[0] if found is not None else None
    return copy.deepcopy(raw) if isinstance(raw, dict) else {}


def mutate_tool_state(
    section: str,
    fn: Callable[[dict[str, Any]], None],
    path: Path | None = None,
    *,
    config_path: Path | None = None,
) -> None:
    """Safely mutate a tool's state ``section`` in ``state.yml`` under its lock.

    ``fn`` receives only the named section's dict to mutate in place; every other
    section — and any keys within this section that ``fn`` does not touch — is
    preserved. Independent tools share one state file (possibly across SDK
    versions), so a write must never drop data it doesn't understand. The section
    is removed when ``fn`` leaves it empty; nothing is written when ``fn``
    changes nothing.

    ``path``/``config_path`` resolve as in :func:`read_tool_state`. When the
    state file lacks the section but the config file still has it at the top
    level, the first changing write moves it: both files are locked (state
    first, then config), the result is written to the state file, and only
    then is the section removed from the config file (a round-trip rewrite that
    keeps comments). A failure before the state write changes nothing. If the
    legacy copy cannot be removed (unwritable, or the config file is a
    symlink), a warning says so and the state file shadows it — an emptied
    section is then kept as ``section: {}`` so the stale copy never returns.
    """
    check_state_section_name(section)
    state_path = path or resolve_state_path()
    legacy_path = _legacy_path(path, config_path)
    with _locked(state_path):
        state = read_config_dict(state_path)
        if section in state or legacy_path is None:
            _write_section(
                state_path,
                state,
                section,
                state.get(section),
                fn,
                keep_empty=lambda: _has_legacy_copy(legacy_path, section),
            )
            return
        with _locked(legacy_path):
            config = read_config_dict(legacy_path)
            if section not in config:
                _write_section(state_path, state, section, None, fn, keep_empty=lambda: False)
                return
            legacy = config[section]
            if legacy is not None and not isinstance(legacy, dict):
                raise ConfigError(
                    f"invalid state: section {section!r} in {legacy_path} must be a "
                    "mapping; fix or remove it before untaped moves it to "
                    f"{state_path}"
                )
            if _write_section(state_path, state, section, legacy, fn, keep_empty=lambda: True):
                _drop_legacy_section(legacy_path, config, section, state_path)


def _legacy_path(path: Path | None, config_path: Path | None) -> Path | None:
    if config_path is not None:
        return config_path
    return resolve_config_path() if path is None else None


def _has_legacy_copy(legacy_path: Path | None, section: str) -> bool:
    """Whether the config file still holds ``section`` (unreadable: assume yes)."""
    if legacy_path is None:
        return False
    try:
        return section in read_config_dict(legacy_path)
    except ConfigError:
        return True


def _write_section(
    target: Path,
    data: dict[str, Any],
    section: str,
    existing: Any,
    fn: Callable[[dict[str, Any]], None],
    *,
    keep_empty: Callable[[], bool],
) -> bool:
    """Apply ``fn`` to ``section`` (seeded from ``existing``); report a change.

    ``data`` is ``target``'s current content; it is written back only when
    ``fn`` changed the section. An emptied section is removed unless
    ``keep_empty()`` says a legacy copy would then resurface.
    """
    if existing is not None and not isinstance(existing, dict):
        raise ConfigError(f"invalid state: section {section!r} in {target} must be a mapping")
    before: dict[str, Any] = copy.deepcopy(existing) if existing is not None else {}
    sub = copy.deepcopy(before)
    fn(sub)
    if sub == before:
        return False
    updated = dict(data)
    if sub or keep_empty():
        updated[section] = sub
    else:
        updated.pop(section, None)
    if updated != data:
        write_config_dict(updated, target)
    get_settings.cache_clear()
    return True


def _drop_legacy_section(
    config_path: Path, config: dict[str, Any], section: str, state_path: Path
) -> None:
    """Remove a migrated state section from the config file (both files locked).

    A failure only warns: the state file already holds (and so shadows) the
    section. After a successful removal an empty placeholder left in the state
    file is dropped.
    """
    problem: str | None = None
    if config_path.is_symlink():
        problem = "it is a symlink, which untaped will not replace"
    else:
        remaining = dict(config)
        del remaining[section]
        try:
            write_config_dict(remaining, config_path)
        except OSError as exc:
            problem = str(exc.strerror or exc)
    get_settings.cache_clear()
    if problem is not None:
        print(
            f"warning: state section {section!r} was saved to {state_path} but could "
            f"not be removed from {config_path} ({problem}); the copy there is now "
            "ignored — delete it by hand.",
            file=sys.stderr,
        )
        return
    state = read_config_dict(state_path)
    if state.get(section) == {}:
        del state[section]
        write_config_dict(state, state_path)


def parse_key(key: str) -> tuple[str, ...]:
    """Convert ``"http.verify_ssl"`` to ``("http", "verify_ssl")``."""
    if not key or key.startswith(".") or key.endswith("."):
        raise ValueError(f"invalid setting key: {key!r}")
    return tuple(key.split("."))


def get_at_path(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    """Return the value at ``path`` or the sentinel ``MISSING``."""
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return MISSING
        cur = cur[key]
    return cur


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
