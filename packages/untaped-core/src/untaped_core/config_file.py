"""Read/write helpers for the ``~/.untaped/config.yml`` file.

These are the lowest-level primitives behind ``untaped config set/unset``.
They never validate against the Settings schema — that's the caller's job.

Note: round-tripping with PyYAML drops comments. Acceptable for v0; if we
need comment preservation later, swap to ``ruamel.yaml``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import SecretStr

from untaped_core.settings import resolve_config_path

_MISSING = object()


def read_config_dict(path: Path | None = None) -> dict[str, Any]:
    """Load the user's config file as a plain dict.

    Returns an empty dict if the file does not exist or is empty.
    """
    target = path or resolve_config_path()
    if not target.is_file():
        return {}
    with target.open() as f:
        loaded = yaml.safe_load(f)
    return loaded if isinstance(loaded, dict) else {}


def write_config_dict(data: dict[str, Any], path: Path | None = None) -> None:
    """Atomically write ``data`` back to the config file.

    Creates parent directories if needed. The file is written with
    permissions ``0o600`` so secrets aren't world-readable.
    """
    target = path or resolve_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=True, default_flow_style=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)


def parse_key(key: str) -> tuple[str, ...]:
    """Convert ``"awx.token"`` to ``("awx", "token")``."""
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


# Sentinel used by :func:`get_at_path` to disambiguate "missing" from "value is None".
MISSING: Any = _MISSING
