"""Read/write helpers for the ``~/.untaped/config.yml`` file.

These are the lowest-level primitives behind the root ``untaped config
set/unset`` commands. They never validate against the Settings schema —
that's the caller's job.

Note: round-tripping with PyYAML drops comments. Acceptable for v0; if we
need comment preservation later, swap to ``ruamel.yaml``.
"""

from __future__ import annotations

import contextlib
import copy
import math
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock, Timeout
from pydantic import SecretStr

from untaped.errors import ConfigError
from untaped.settings import get_settings, load_config_yaml, resolve_config_path

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

    Creates parent directories if needed. The data is written to a unique
    temp file created with permissions ``0o600`` (so secrets are never
    world-readable, even briefly) and atomically renamed over the target;
    a failed write leaves the original untouched and no temp file behind.
    """
    target = path or resolve_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, sort_keys=True, default_flow_style=False)
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
    target.parent.mkdir(parents=True, exist_ok=True)
    timeout = _lock_timeout()
    lock = FileLock(str(target) + ".lock", timeout=timeout)
    try:
        lock.acquire()
    except Timeout as exc:
        raise ConfigError(
            f"could not acquire lock on {target}; another untaped process is "
            f"writing to it (waited {timeout}s)."
        ) from exc
    try:
        data = read_config_dict(target)
        before = copy.deepcopy(data)
        fn(data)
        if data != before:
            write_config_dict(data, target)
            get_settings.cache_clear()
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


def read_tool_state(section: str, path: Path | None = None) -> dict[str, Any]:
    """Return a copy of a tool's top-level state ``section`` dict, or ``{}``."""
    raw = read_config_dict(path).get(section)
    return copy.deepcopy(raw) if isinstance(raw, dict) else {}


def mutate_tool_state(
    section: str, fn: Callable[[dict[str, Any]], None], path: Path | None = None
) -> None:
    """Safely mutate a tool's top-level state ``section`` under the config lock.

    ``fn`` receives only the named section's dict to mutate in place; every other
    section — and any keys within this section that ``fn`` does not touch — is
    preserved. Independent tools share one config file (possibly across SDK
    versions), so a write must never drop data it doesn't understand. The section
    is removed when ``fn`` leaves it empty.
    """

    def _apply(data: dict[str, Any]) -> None:
        existing = data.get(section)
        sub: dict[str, Any] = existing if isinstance(existing, dict) else {}
        fn(sub)
        if sub:
            data[section] = sub
        elif isinstance(existing, dict):
            del data[section]

    mutate_config(_apply, path)


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
