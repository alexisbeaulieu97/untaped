"""Naming rules: workspace names, repo identities, directory names, branches."""

from __future__ import annotations

from collections.abc import Sequence

from untaped.sdk import UsageError, q, repo_url_parts, safe_path_segment
from untaped_workspace.domain.models import RepoSpec


def validate_workspace_name(name: str) -> str:
    """Return ``name`` if it is one safe path segment, else raise ``UsageError``."""
    if not name or name.startswith(".") or safe_path_segment(name) != name:
        raise UsageError(f"invalid workspace name {q(name)}: use letters, digits, '.', '_' or '-'")
    return name


def looks_like_url(ident: str) -> bool:
    """Whether ``ident`` is a URL, scp-style ``user@host:path``, a path, or ends in ``.git``."""
    return (
        "://" in ident
        or ident.startswith(("/", "~"))
        or ident.endswith(".git")
        or repo_url_parts(ident)[0] is not None
    )


def repo_identity(url: str) -> tuple[str, str]:
    """``(owner, name)`` from a clone URL or path; owner is ``""`` when absent."""
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
