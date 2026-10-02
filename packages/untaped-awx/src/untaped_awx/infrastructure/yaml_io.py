"""Serialise / deserialise :class:`Resource` envelopes from YAML files.

Single-doc and multi-doc YAML are both supported. ``read_resource_files``
also accepts a directory and walks every ``*.yml`` / ``*.yaml`` it finds;
``read_resource_files_at`` does the same at a git commit, and
``read_resource_text`` parses documents already read (piped on stdin).
A missing or invalid input file is a :class:`ConfigError` whose category
(``not_found`` / ``invalid``) exits ``1``: the input, not the setup, is wrong.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import yaml

from untaped.sdk import ConfigError
from untaped_awx.domain import Resource
from untaped_awx.infrastructure.git_source import GitSource


def read_resource_files(path: Path) -> Iterator[tuple[Path, Resource]]:
    """Yield each :class:`Resource` in ``path`` paired with its source file.

    ``path`` may be a single ``.yml`` (one or many docs) or a directory
    walked recursively for ``*.yml`` and ``*.yaml``. Empty docs are skipped.
    """
    p = path.expanduser()
    if not p.exists():
        raise ConfigError(f"file not found: {p}", category="not_found")
    files = sorted([*p.rglob("*.yml"), *p.rglob("*.yaml")]) if p.is_dir() else [p]
    if p.is_dir() and not files:
        raise ConfigError(f"no .yml/.yaml files found under {p}", category="not_found")
    for f in files:
        for resource in _read_file(f):
            yield f, resource


def read_resource_files_at(
    source: GitSource, path: Path, *, skip: Callable[[str, str], bool] | None = None
) -> list[tuple[str, Resource]]:
    """Each :class:`Resource` of the files ``path`` names at ``source``'s commit.

    Each comes with the ``REF:PATH`` it was read from. ``skip(rel, text)``
    leaves a file out (its repo-relative path and its text).
    """
    found: list[tuple[str, Resource]] = []
    for rel in source.files(path):
        text = source.read_text(rel)
        if skip is not None and skip(rel, text):
            continue
        label = source.label(rel)
        found.extend((label, doc) for doc in read_resource_text(text, source=label))
    return found


def _read_file(path: Path) -> Iterator[Resource]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}", category="invalid") from exc
    return read_resource_text(text, source=str(path))


def read_resource_text(text: str, *, source: str) -> Iterator[Resource]:
    """Yield each :class:`Resource` in YAML ``text``; errors name ``source``."""
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {source}: {exc}", category="invalid") from exc
    for doc in docs:
        if doc is None:
            continue
        if not isinstance(doc, dict):
            raise ConfigError(f"{source}: each YAML doc must be a mapping", category="invalid")
        try:
            yield Resource.model_validate(doc)
        except Exception as exc:
            raise ConfigError(f"{source}: {exc}", category="invalid") from exc


class _DocumentDumper(yaml.SafeDumper):
    """Safe dumper that writes multi-line strings as ``|`` literal blocks.

    PyYAML falls back to a quoted scalar when a block cannot hold the value
    exactly (trailing spaces, some control characters), so output stays lossless.
    """


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    # The loader folds NEL and the Unicode line/paragraph separators into "\n";
    # only double quotes escape them, so force that style for such strings.
    folds = any(c in value for c in "\x85\u2028\u2029")
    style = '"' if folds else ("|" if "\n" in value else None)
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_DocumentDumper.add_representer(str, _represent_str)


def dump_resource(
    resource: Resource, *, header_comment: str | None = None, comment: str | None = None
) -> str:
    """Return the YAML representation of ``resource``.

    ``header_comment`` is one ``#`` line; ``comment`` follows it, one ``#``
    line per line of text.
    """
    payload = resource.model_dump(exclude_none=True)
    if resource.metadata.organization is None and "organization" in (
        resource.metadata.model_fields_set
    ):
        # Explicit null identity (org-less record) survives the round trip.
        payload["metadata"] = {"name": resource.metadata.name, "organization": None} | payload[
            "metadata"
        ]
    body = yaml.dump(
        payload,
        Dumper=_DocumentDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    lines = ([header_comment] if header_comment else []) + (comment.splitlines() if comment else [])
    prefix = "".join(f"# {line}\n" if line else "#\n" for line in lines)
    return prefix + body
