"""Find where a repository appears in a root's downstream dependency graph.

Pure over :class:`DependencyGraph`: the root's downstream walk
(:func:`~untaped.capabilities.ansible.domain.reach.reach`) is filtered to the
wanted repositories, so each reported path is a shortest one. A match is one
node of a wanted repository; its declared ref is the ``version`` string on
the edge reaching it, reported verbatim (``None`` when unpinned). A match
carries the identity of the input that named its root, so a pipeline can
join results back to its inputs.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from untaped.capabilities.ansible.domain.graph import DependencyGraph
from untaped.capabilities.ansible.domain.graph_roots import RootInput
from untaped.capabilities.ansible.domain.identity import repo_key
from untaped.capabilities.ansible.domain.reach import reach


class DependencyMatch(BaseModel):
    """One wanted repository reached from one root (``ansible.dependency_match``)."""

    model_config = ConfigDict(frozen=True)
    table_columns: ClassVar[tuple[str, ...]] = (
        "root_repo",
        "root_ref",
        "repo",
        "declared_ref",
        "path",
    )

    root_repo: str
    root_ref: str | None
    repo: str
    declared_ref: str | None
    declared_in: str | None
    path: list[str]
    input_kind: str | None = None
    input_id: int | str | None = None
    input_name: str | None = None


def find_matches(
    graph: DependencyGraph, wanted: Collection[str], *, source: RootInput | None = None
) -> list[DependencyMatch]:
    """Every node of a ``wanted`` repo (``owner/name``) downstream of the graph's root."""
    wanted_keys = {repo_key(repo) for repo in wanted}
    identity = source or RootInput()
    return [
        DependencyMatch(
            root_repo=hit.root.repo or hit.root.label,
            root_ref=hit.root.ref,
            repo=hit.node.repo,
            declared_ref=hit.node.declared_ref,
            declared_in=hit.node.declared_in,
            path=hit.node.path,
            input_kind=identity.kind,
            input_id=identity.id,
            input_name=identity.name,
        )
        for hit in reach(graph, "requires")
        if hit.node.repo is not None and repo_key(hit.node.repo) in wanted_keys
    ]


__all__ = ["DependencyMatch", "find_matches"]
