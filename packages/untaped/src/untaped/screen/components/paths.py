"""Filesystem completion for ``PathInput``.

:func:`complete_paths` lists what could finish the path typed so far. It keeps
the text the user typed, ``~`` included, and only expands it to find the
directory to list, so accepting a candidate never rewrites what they wrote.
"""

from __future__ import annotations

import os

__all__ = ["MAX_CANDIDATES", "MAX_SCANNED", "complete_paths"]

#: The most candidates one completion returns.
MAX_CANDIDATES = 50
#: The most directory entries one completion looks at, so a huge directory stays cheap to type in.
MAX_SCANNED = 500


def complete_paths(text: str) -> tuple[str, ...]:
    """The paths that start with ``text``, directories first and each ending in a separator.

    Nothing is offered for an empty ``text``. Hidden entries are offered only
    once the last segment starts with a dot. An unreadable directory offers
    nothing, and an entry that cannot be inspected (a symlink loop) is offered
    as a file instead of hiding the others. Only the first ``MAX_SCANNED``
    entries the filesystem lists are looked at, so in a larger directory some
    matches may not be offered until more of the name is typed.
    """
    if not text:
        return ()
    cut = max(text.rfind("/"), text.rfind(os.sep)) + 1
    typed_directory, prefix = text[:cut], text[cut:]
    directory = os.path.expanduser(typed_directory) if typed_directory else "."
    found: list[tuple[bool, str]] = []
    try:
        with os.scandir(directory) as entries:
            for scanned, entry in enumerate(entries):
                if scanned >= MAX_SCANNED:
                    break
                if not entry.name.startswith(prefix):
                    continue
                if entry.name.startswith(".") and not prefix.startswith("."):
                    continue
                found.append((_is_directory(entry), entry.name))
    except OSError:
        return ()
    found.sort(key=lambda item: (not item[0], item[1]))
    return tuple(
        typed_directory + name + (os.sep if is_dir else "")
        for is_dir, name in found[:MAX_CANDIDATES]
    )


def _is_directory(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False
