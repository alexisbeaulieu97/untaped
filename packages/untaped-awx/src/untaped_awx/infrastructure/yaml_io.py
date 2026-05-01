"""Serialise / deserialise :class:`Resource` envelopes from YAML files.

Single-doc and multi-doc YAML are both supported. ``read_resources``
also accepts a directory and walks every ``*.yml`` it finds.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import yaml
from untaped_core import ConfigError

from untaped_awx.domain import Resource


def read_resources(path: Path) -> Iterator[Resource]:
    """Yield :class:`Resource` objects from ``path``.

    ``path`` may be a single ``.yml`` (one or many docs) or a directory
    walked recursively for ``*.yml``. Empty docs are skipped.
    """
    p = path.expanduser()
    if not p.exists():
        raise ConfigError(f"file not found: {p}")
    files = sorted(p.rglob("*.yml")) if p.is_dir() else [p]
    if p.is_dir() and not files:
        raise ConfigError(f"no .yml files found under {p}")
    for f in files:
        yield from _read_file(f)


def write_resource(
    path: Path,
    resource: Resource,
    *,
    header_comment: str | None = None,
) -> None:
    """Write a single resource to ``path`` as YAML.

    ``header_comment`` is emitted as a leading ``#`` line — used to
    flag partial-fidelity saves.
    """
    p = path.expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_dump(resource, header_comment=header_comment))


def write_resources(path: Path, resources: Iterable[Resource]) -> None:
    """Write multiple resources to ``path`` as a multi-doc YAML stream."""
    p = path.expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    for r in resources:
        chunks.append(_dump(r))
    p.write_text("---\n".join(chunks))


def dump_resource(resource: Resource, *, header_comment: str | None = None) -> str:
    """Return the YAML representation of ``resource`` as a string (for stdout)."""
    return _dump(resource, header_comment=header_comment)


def _read_file(path: Path) -> Iterator[Resource]:
    text = path.read_text()
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


def _dump(resource: Resource, *, header_comment: str | None = None) -> str:
    payload = resource.model_dump(exclude_none=True)
    body = yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)
    if header_comment:
        return f"# {header_comment}\n{body}"
    return body


def to_dict(payload: Any) -> dict[str, Any]:
    """Lift ``payload`` to ``dict`` if it's a :class:`Resource`-like model."""
    if hasattr(payload, "model_dump"):
        return dict(payload.model_dump())
    return dict(payload)
