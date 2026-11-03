"""Naming rules: workspace names, repo identities, directory names, branches."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from untaped.sdk import UsageError, q
from untaped.sdk.storage import safe_path_segment
from untaped_workspace.domain.models import RepoSpec

#: The longest name :func:`~untaped.sdk.safe_path_segment` keeps as it is.
_NAME_MAX_BYTES = 128


def workspace_key(name: str) -> str:
    """``name`` NFC-normalised: how a typed name finds the workspace ``create`` stored."""
    return unicodedata.normalize("NFC", name)


def validate_workspace_name(name: str) -> str:
    """``name`` NFC-normalised if it is one safe path segment, else raise ``UsageError``.

    Normalised first, so a decomposed name (as macOS file names often are)
    is the same workspace as its composed spelling.
    """
    name = workspace_key(name)
    if not name or name.startswith(".") or safe_path_segment(name) != name:
        if name and len(name.encode()) > _NAME_MAX_BYTES:
            raise UsageError(f"invalid workspace name {q(name)}: at most {_NAME_MAX_BYTES} bytes")
        raise UsageError(f"invalid workspace name {q(name)}: use letters, digits, '.', '_' or '-'")
    return name


def repo_key(url: str) -> tuple[str, ...]:
    """Which repo ``url`` names: its repo store key.

    The store keeps one repo per key (the https and ssh URLs of a repo, and
    two spellings of its path, are one), so workspace counts them as one too.
    """
    from untaped_git.api import store_key  # noqa: PLC0415  # keeps CLI startup free of git

    return store_key(url)


def store_ident(url: str) -> str:
    """``url``'s repo store key as one string, ``host/owner/name`` (no ``.git``)."""
    key = repo_key(url)
    return "/".join((*key[:-1], key[-1].removesuffix(".git")))


def repo_identity(url: str) -> tuple[str, str]:
    """``(owner, name)`` from a clone URL or path; owner is ``""`` when absent."""
    from untaped_git.api import repo_url_parts  # noqa: PLC0415  # keeps CLI startup free of git

    _, segments = repo_url_parts(url)
    name = segments[-1] if segments else url
    owner = segments[-2] if len(segments) > 1 else ""
    return owner, name


def assign_dirs(new: Sequence[tuple[str, str]], existing: Sequence[RepoSpec]) -> list[str]:
    """Directory names for ``new`` repos: the repo name, ``owner-name`` on a clash."""
    taken = {spec.dir for spec in existing}
    dirs: list[str] = []
    for owner, name in new:
        clash = name in taken
        candidate = safe_path_segment(f"{owner}-{name}" if clash and owner else name)
        while candidate in taken:
            candidate = f"{candidate}-{len(taken)}"
        taken.add(candidate)
        dirs.append(candidate)
    return dirs


def branch_for(template: str, workspace: str) -> str:
    """The branch a writable repo gets: ``template`` with ``{name}`` filled in."""
    return template.replace("{name}", workspace)
