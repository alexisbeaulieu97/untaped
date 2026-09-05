"""Shared manifest repo selection helpers."""

from __future__ import annotations

from collections.abc import Sequence

from untaped_workspace.domain import Repo, WorkspaceManifest


def select_repos(
    manifest: WorkspaceManifest,
    identifiers: Sequence[str] | None,
) -> tuple[list[Repo], tuple[str, ...]]:
    """Return manifest repos matching ``identifiers`` plus unmatched values.

    Identifiers may be repo names or URLs. Matched repos keep manifest order
    so command output stays stable regardless of option order.
    """
    if not identifiers:
        return list(manifest.repos), ()
    wanted = set(identifiers)
    known = {repo.name for repo in manifest.repos} | {repo.url for repo in manifest.repos}
    matched = [repo for repo in manifest.repos if repo.name in wanted or repo.url in wanted]
    unmatched = tuple(sorted(wanted - known))
    return matched, unmatched
