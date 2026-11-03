"""Deep merge of one structured document into another, and the keys it manages.

Mappings recurse; scalars and lists in the source replace the target's. Keys
the source does not mention are untouched. The merge mutates the target
mapping in place so a comment-preserving YAML or TOML document keeps its
comments; the document's format shapes what it writes (``fit``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, MutableMapping
from typing import Any

from untaped_dotfiles.models import KeyPath

#: ``fit(parent, value)``: ``value`` shaped the way ``parent``'s document can hold it.
type Fit = Callable[[MutableMapping[str, Any], Any], Any]


def as_is(parent: MutableMapping[str, Any], value: Any) -> Any:
    """The :data:`Fit` of a document that holds any value as given."""
    return value


def merge_into(
    target: MutableMapping[str, Any], source: Mapping[str, Any], *, fit: Fit = as_is
) -> None:
    """Merge ``source`` into ``target`` in place; ``fit(parent, value)`` shapes what it writes."""
    _merge(target, (), source, fit)


def _merge(
    root: MutableMapping[str, Any], path: KeyPath, source: Mapping[str, Any], fit: Fit
) -> None:
    for key, value in source.items():
        # Looked up again for each key: a tomlkit table view goes stale once written through.
        node = _at(root, path)
        current = node.get(key)
        if isinstance(value, Mapping) and isinstance(current, MutableMapping):
            _merge(root, (*path, key), value, fit)
        elif isinstance(value, Mapping):
            fresh: dict[str, Any] = {}
            merge_into(fresh, value)
            node[key] = fit(node, fresh)
        else:
            node[key] = fit(node, value)


def _at(root: MutableMapping[str, Any], path: KeyPath) -> MutableMapping[str, Any]:
    node = root
    for key in path:
        node = node[key]
    return node


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
