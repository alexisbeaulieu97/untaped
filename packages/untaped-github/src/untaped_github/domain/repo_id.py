"""A GitHub repository id: ``owner/name``."""

from __future__ import annotations


def split_repo_id(value: str) -> tuple[str, str] | None:
    """``(owner, name)`` of a GitHub ``owner/name`` id, or ``None`` when it isn't one.

    Exactly one slash between two non-empty parts. A path such as
    ``./local`` or ``roles/..`` is not an id: an owner never starts with a
    dot and a name is never ``.`` or ``..``.
    """
    owner, separator, name = value.partition("/")
    if not owner or not separator or not name or "/" in name:
        return None
    if owner.startswith(".") or name in {".", ".."}:
        return None
    return owner, name
