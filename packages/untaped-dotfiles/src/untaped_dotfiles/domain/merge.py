"""Deep merge of one structured document into another, and the keys it manages.

Mappings recurse; scalars and lists in the source replace the target's. Keys
the source does not mention are untouched. The merge mutates the target
mapping in place so a comment-preserving YAML document keeps its comments.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping
from typing import Any

from untaped_dotfiles.domain.models import KeyPath


def merge_into(target: MutableMapping[str, Any], source: Mapping[str, Any]) -> None:
    """Merge ``source`` into ``target`` in place."""
    for key, value in source.items():
        current = target.get(key)
        if isinstance(value, Mapping) and isinstance(current, MutableMapping):
            merge_into(current, value)
        elif isinstance(value, Mapping):
            fresh: dict[str, Any] = {}
            merge_into(fresh, value)
            target[key] = fresh
        else:
            target[key] = value


def leaf_paths(source: Mapping[str, Any], prefix: KeyPath = ()) -> Iterator[KeyPath]:
    """The key paths ``merge_into`` writes: every non-mapping value, and every empty mapping."""
    for key, value in source.items():
        path = (*prefix, str(key))
        if isinstance(value, Mapping) and value:
            yield from leaf_paths(value, path)
        else:
            yield path


_ABSENT = object()


def lookup(document: Mapping[str, Any], path: KeyPath) -> Any:
    """The value at ``path``, or :data:`_ABSENT` when any key is missing."""
    node: Any = document
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return _ABSENT
        node = node[key]
    return node


def project(document: Mapping[str, Any], paths: tuple[KeyPath, ...]) -> list[Any]:
    """The values at ``paths``, in order, ``None`` where a path is absent."""
    return [None if (found := lookup(document, path)) is _ABSENT else found for path in paths]


def remove_paths(document: MutableMapping[str, Any], paths: tuple[KeyPath, ...]) -> None:
    """Delete the keys at ``paths`` in place; a mapping left empty by that is deleted too."""
    for path in paths:
        _remove(document, path)


def _remove(node: MutableMapping[str, Any], path: KeyPath) -> None:
    if not path:
        return
    head, *rest = path
    if head not in node:
        return
    if rest:
        child = node[head]
        if isinstance(child, MutableMapping):
            _remove(child, tuple(rest))
            if child:
                return
    del node[head]
