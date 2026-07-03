"""Filesystem input helpers for SDK commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from untaped.errors import ConfigError


def read_structured_file(path: Path) -> dict[str, Any]:
    """Read a YAML-or-JSON mapping file (``.json`` suffix → JSON parser).

    Raises :class:`ConfigError` on read failure, parse failure, or a
    non-mapping document. An empty document is an empty dict.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc
    try:
        raw = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"could not parse {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain an object")
    return dict(raw)
