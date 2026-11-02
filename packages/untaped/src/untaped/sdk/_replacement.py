"""A deprecation's replacement as text.

A command found in the mounted tree (so a warning keeps naming it after a rename), a
setting key or prose.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from cyclopts import App
from cyclopts.command_spec import CommandSpec

from untaped.sdk.marks import _ROOT_NAME, Deprecated

COMMAND_TEXT = re.compile(r"^untaped(?: [a-z0-9][a-z0-9-]*)+$")


KEY_TEXT = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")


def children(app: App, *, resolve: bool) -> Iterator[tuple[str, App]]:
    """The mounted subcommands of ``app``, the way cyclopts' ``groups_from_app`` walks them.

    An unresolved lazy command is skipped unless ``resolve`` (reading it would
    import its plugin). A sub-app mounted under two names appears once.
    """
    seen: set[int] = set()
    for name in app:
        if name.startswith("-"):
            continue
        item = app._get_item(name, recurse_meta=True)
        if isinstance(item, CommandSpec) and not item.is_resolved and not resolve:
            continue
        sub = app[name]
        if id(sub) not in seen:
            seen.add(id(sub))
            yield name, sub


def replacement_path(
    tree: App, replacement: object, *, prefix: tuple[str, ...] = (_ROOT_NAME,)
) -> tuple[str, ...] | None:
    """The command path of ``replacement`` (a command function or app) within ``tree``.

    Depth-first over the resolved commands; ``prefix`` is where ``tree`` hangs
    from the root. ``None`` when the object is not mounted.
    """
    return _find(tree, replacement, prefix, set())


def _find(
    app: App, replacement: object, path: tuple[str, ...], visited: set[int]
) -> tuple[str, ...] | None:
    if id(app) in visited:
        return None
    visited.add(id(app))
    for name, sub in children(app, resolve=False):
        here = (*path, name)
        if sub is replacement or (
            sub.default_command is not None and sub.default_command is replacement
        ):
            return here
        found = _find(sub, replacement, here, visited)
        if found is not None:
            return found
    return None


def replacement_text(
    mark: Deprecated, tree: App | None, prefix: tuple[str, ...] = (_ROOT_NAME,)
) -> str | None:
    """The replacement as a help or warning shows it, or ``None`` when there is none.

    An object becomes `` `untaped <path>` ``; a text shaped like a command, a
    setting key or a flag (``--limit 1 --yes``) is backticked; prose stays as
    is. An object that is not found in ``tree`` (or no ``tree``) gives ``None``
    (an SDK callable's replacement is :func:`sdk_replacement_text`'s).
    """
    replacement = mark.replacement
    if replacement is None:
        return None
    if isinstance(replacement, str):
        shaped = (
            COMMAND_TEXT.match(replacement)
            or KEY_TEXT.match(replacement)
            or replacement.startswith("-")
        )
        return f"`{replacement}`" if shaped else replacement
    path = None if tree is None else replacement_path(tree, replacement, prefix=prefix)
    return None if path is None else f"`{' '.join(path)}`"


def sdk_replacement_text(mark: Deprecated) -> str | None:
    """The replacement of a deprecated SDK callable or parameter, as its warning shows it.

    An object is named by its backticked ``__qualname__`` (``Diagnostics.failure``),
    so a rename breaks the import rather than the text; text reads as
    :func:`replacement_text` shows it.
    """
    replacement = mark.replacement
    if replacement is None or isinstance(replacement, str):
        return replacement_text(mark, None)
    qualname = getattr(replacement, "__qualname__", None)
    return None if qualname is None else f"`{qualname}`"
