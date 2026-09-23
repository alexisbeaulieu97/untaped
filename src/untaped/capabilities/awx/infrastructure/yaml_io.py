"""Serialise / deserialise :class:`Resource` envelopes from YAML files.

Single-doc and multi-doc YAML are both supported. ``read_resource_files``
also accepts a directory and walks every ``*.yml`` / ``*.yaml`` it finds.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import yaml

from untaped.capabilities.awx.domain import Resource
from untaped.capability_api import ConfigError


def read_resource_files(path: Path) -> Iterator[tuple[Path, Resource]]:
    """Yield each :class:`Resource` in ``path`` paired with its source file.

    ``path`` may be a single ``.yml`` (one or many docs) or a directory
    walked recursively for ``*.yml`` and ``*.yaml``. Empty docs are skipped.
    """
    p = path.expanduser()
    if not p.exists():
        raise ConfigError(f"file not found: {p}")
    files = sorted([*p.rglob("*.yml"), *p.rglob("*.yaml")]) if p.is_dir() else [p]
    if p.is_dir() and not files:
        raise ConfigError(f"no .yml/.yaml files found under {p}")
    for f in files:
        for resource in _read_file(f):
            yield f, resource


def _read_file(path: Path) -> Iterator[Resource]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    for doc in docs:
        if doc is None:
            continue
        if not isinstance(doc, dict):
            raise ConfigError(f"{path}: each YAML doc must be a mapping")
        try:
            yield Resource.model_validate(doc)
        except Exception as exc:
            raise ConfigError(f"{path}: {exc}") from exc


def dump_resource(resource: Resource, *, header_comment: str | None = None) -> str:
    """Return the YAML representation of ``resource`` (``header_comment`` as a ``#`` line)."""
    payload = resource.model_dump(exclude_none=True)
    if resource.metadata.organization is None and "organization" in (
        resource.metadata.model_fields_set
    ):
        # Explicit null identity (org-less record) survives the round trip.
        payload["metadata"] = {"name": resource.metadata.name, "organization": None} | payload[
            "metadata"
        ]
    body = yaml.safe_dump(payload, sort_keys=False, default_flow_style=False, allow_unicode=True)
    if header_comment:
        return f"# {header_comment}\n{body}"
    return body
