"""Comment-preserving YAML rewrites for ``config.yml``.

:func:`render_preserving` turns the file's current text plus a before/after
pair of plain dicts (as PyYAML reads them) into new text in which only the
changed keys are rewritten: comments, key order, quoting and flow style of
everything else survive. ``ruamel.yaml`` does the round trip; PyYAML stays the
reader of record, so every write is checked to read back as ``after`` and
falls back to a plain dump otherwise. ``ruamel.yaml`` is imported lazily.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Any

import yaml


def render_preserving(
    original: str | None, before: Mapping[str, Any], after: dict[str, Any]
) -> str:
    """Return YAML text for ``after``, keeping ``original``'s untouched formatting.

    ``before`` is what PyYAML read from ``original``. Without an original
    document (absent, empty, or unparseable by ``ruamel.yaml``) this is a
    plain dump of ``after`` in insertion order.
    """
    if original:
        try:
            text = _round_trip(original, before, after)
        except Exception:  # any ruamel failure falls back to a plain dump
            text = None
        if text is not None and _reads_back(text, after):
            return text
    return plain_dump(after)


def plain_dump(data: Mapping[str, Any]) -> str:
    """Dump ``data`` with PyYAML in insertion order (no comments to keep)."""
    return yaml.safe_dump(dict(data), sort_keys=False, default_flow_style=False)


def _reads_back(text: str, expected: Mapping[str, Any]) -> bool:
    try:
        return bool(yaml.safe_load(text) == expected)
    except yaml.YAMLError:
        return False


def _round_trip(original: str, before: Mapping[str, Any], after: dict[str, Any]) -> str | None:
    from ruamel.yaml import YAML  # noqa: PLC0415
    from ruamel.yaml.comments import CommentedMap  # noqa: PLC0415
    from ruamel.yaml.util import load_yaml_guess_indent  # noqa: PLC0415

    rt = YAML()
    rt.preserve_quotes = True
    rt.width = 4096
    doc, seq_indent, seq_offset = load_yaml_guess_indent(original, yaml=rt)
    if not isinstance(doc, CommentedMap):
        return None
    _sync_map(doc, before, after)
    rt.indent(
        mapping=_mapping_indent(original),
        sequence=seq_indent or 2,
        offset=seq_offset or 0,
    )
    out = io.StringIO()
    rt.dump(doc, out)
    return out.getvalue()


def _mapping_indent(text: str) -> int:
    """Guess the mapping indent: the shallowest indented, non-sequence line."""
    widths = [
        len(line) - len(line.lstrip(" "))
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "-"))
    ]
    positive = [width for width in widths if width > 0]
    return min(positive) if positive else 2


def _sync_map(node: Any, before: Mapping[Any, Any], after: Mapping[Any, Any]) -> None:
    """Make ruamel map ``node`` (read as ``before``) hold ``after``, in place."""
    if set(node) != set(before):
        # Keys read differently by the two parsers (e.g. ``on:``): rewrite whole.
        node.clear()
        for key, value in after.items():
            node[_fresh(key)] = _fresh(value)
        return
    carried = _carry_renames(node, before, after)
    for key in [key for key in node if key not in after]:
        del node[key]
    for key, value in after.items():
        if key in carried or (key in before and before[key] == value):
            continue
        if key in node and _sync_child(node, key, before[key], value):
            continue
        node[_fresh(key)] = _fresh(value)


def _carry_renames(node: Any, before: Mapping[Any, Any], after: Mapping[Any, Any]) -> set[Any]:
    """Rename keys in place when a dropped key's container moved to a new key.

    ``profile rename`` pops ``profiles.<old>`` and re-adds it as ``<new>``;
    reusing the ruamel child keeps its comments, formatting and position.
    Returns the new keys that were carried over (already equal to ``after``).
    """
    carried: set[Any] = set()
    gone = [key for key in node if key not in after and isinstance(before[key], dict | list)]
    for key, value in after.items():
        if key in before:
            continue
        match = next((old for old in gone if before[old] == value), None)
        if match is None:
            continue
        gone.remove(match)
        position = list(node).index(match)
        child = node.pop(match)
        comment = node.ca.items.pop(match, None)
        node.insert(position, _fresh(key), child)
        if comment is not None:
            node.ca.items[key] = comment
        carried.add(key)
    return carried


def _sync_seq(node: Any, before: list[Any], after: list[Any]) -> None:
    """Make ruamel sequence ``node`` (read as ``before``) hold ``after``, in place."""
    if len(node) != len(before):
        node[:] = [_fresh(item) for item in after]
        return
    shared = min(len(before), len(after))
    for index in range(shared):
        if before[index] == after[index]:
            continue
        if not _sync_child(node, index, before[index], after[index]):
            node[index] = _fresh(after[index])
    del node[shared:]
    node.extend(_fresh(item) for item in after[shared:])


def _sync_child(parent: Any, key: Any, before: Any, after: Any) -> bool:
    """Recurse into a container child; ``False`` when it must be replaced."""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq  # noqa: PLC0415

    child = parent[key]
    if isinstance(child, CommentedMap) and isinstance(before, dict) and isinstance(after, dict):
        _sync_map(child, before, after)
        return True
    if isinstance(child, CommentedSeq) and isinstance(before, list) and isinstance(after, list):
        _sync_seq(child, before, after)
        return True
    return False


def _fresh(value: Any) -> Any:
    """Convert a new plain value so PyYAML reads it back with the same type.

    Strings PyYAML would resolve to something else (``no``, ``0123``, ``~``,
    ``1:30``, ...) are single-quoted.
    """
    from ruamel.yaml.scalarstring import SingleQuotedScalarString  # noqa: PLC0415

    if isinstance(value, str):
        return SingleQuotedScalarString(value) if _needs_quotes(value) else value
    if isinstance(value, Mapping):
        return {_fresh(key): _fresh(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_fresh(item) for item in value]
    return value


def _needs_quotes(value: str) -> bool:
    try:
        return bool(yaml.safe_load(value) != value)
    except yaml.YAMLError:
        return True
