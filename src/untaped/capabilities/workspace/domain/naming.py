"""Naming rules: workspace names, repo identities, directory names, branches."""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlparse

from untaped.capabilities.workspace.domain.models import RepoSpec
from untaped.capability_api import UsageError, q, safe_path_segment

_SCP = re.compile(r"^[^@/]+@[^:/]+:(?P<path>.+)$")


def validate_workspace_name(name: str) -> str:
    """Return ``name`` if it is one safe path segment, else raise ``UsageError``."""
    if not name or name.startswith(".") or safe_path_segment(name) != name:
        raise UsageError(f"invalid workspace name {q(name)}: use letters, digits, '.', '_' or '-'")
    return name


def repo_identity(url: str) -> tuple[str, str]:
    """``(owner, name)`` from a clone URL or path; owner is ``""`` when absent."""
    match = _SCP.match(url)
    path = match.group("path") if match else (urlparse(url).path if "://" in url else url)
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    name = parts[-1].removesuffix(".git") if parts else url
    owner = parts[-2] if len(parts) > 1 else ""
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
