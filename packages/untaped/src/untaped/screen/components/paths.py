"""Filesystem completion for ``PathInput``.

:func:`complete_paths` lists what could finish the path typed so far. It keeps
the text the user typed, ``~`` included, and only expands it to find the
directory to list, so accepting a candidate never rewrites what they wrote.
"""

from __future__ import annotations

import os

__all__ = ["MAX_CANDIDATES", "complete_paths"]

#: The most candidates one completion returns.
MAX_CANDIDATES = 50


def complete_paths(text: str) -> tuple[str, ...]:
    """The paths that start with ``text``, directories first and each ending in a separator.

    Nothing is offered for an empty ``text``. Hidden entries are offered only
    once the last segment starts with a dot. An unreadable directory offers nothing.
    """
    if not text:
        return ()
    cut = max(text.rfind("/"), text.rfind(os.sep)) + 1
    typed_directory, prefix = text[:cut], text[cut:]
    directory = os.path.expanduser(typed_directory) if typed_directory else "."
    try:
        with os.scandir(directory) as entries:
            found = [
                (not entry.is_dir(), entry.name, entry.is_dir())
                for entry in entries
                if entry.name.startswith(prefix)
                and (prefix.startswith(".") or not entry.name.startswith("."))
            ]
    except OSError:
        return ()
    found.sort()
    return tuple(
        typed_directory + name + (os.sep if is_dir else "")
        for _, name, is_dir in found[:MAX_CANDIDATES]
    )
