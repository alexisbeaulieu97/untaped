"""Utilities for loading YAML content from disk or strings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import YamlLoadError


def read_yaml_text(path: str | Path) -> str:
    """Read YAML file contents as text, raising :class:`YamlLoadError` on failure."""

    file_path = Path(path)
    try:
        return file_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - relies on filesystem failures
        raise YamlLoadError(f"Unable to read YAML file '{file_path}': {exc}") from exc


def load_yaml_string(content: str, *, source: str | Path | None = None) -> Any:
    """Parse YAML from a string, annotating errors with the optional source."""

    try:
        return yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - difficult to trigger deterministically
        location = f" from '{source}'" if source else ""
        raise YamlLoadError(f"Invalid YAML{location}: {exc}") from exc


def load_yaml_file(path: str | Path) -> Any:
    """Load YAML data from ``path`` with robust error handling."""

    file_path = Path(path)
    text = read_yaml_text(file_path)
    return load_yaml_string(text, source=file_path)


def load_variables_file(path: str | Path) -> dict[str, Any]:
    """Load a variables YAML file, asserting the root element is a mapping."""

    data = load_yaml_file(path)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise YamlLoadError(
            f"Variables file '{path}' must contain a mapping at the top level, received {type(data).__name__}",
        )
    return data
