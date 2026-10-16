"""Find where a repository appears in a root's downstream dependency graph.

Pure over :class:`DependencyGraph`: the root's downstream walk
(:func:`~untaped_ansible.domain.reach.reach`) is filtered to the
wanted repositories, so each reported path is a shortest one. A match is one
node of a wanted repository; its declared ref is the ``version`` string on
the edge reaching it, reported verbatim (``None`` when unpinned). A match
carries the identity of the input that named its root, so a pipeline can
join results back to its inputs.
"""

from __future__ import annotations

from collections.abc import Collection

from untaped_ansible.domain.graph import DependencyGraph
from untaped_ansible.domain.graph_roots import RootInput
from untaped_ansible.domain.identity import repo_key
from untaped_ansible.domain.reach import reach
from untaped_ansible.records import DependencyMatch


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
            repo=hit.row.repo,
            declared_ref=hit.row.declared_ref,
            declared_in=hit.row.declared_in,
            path=hit.row.path,
            input_kind=identity.kind,
            input_id=identity.id,
            input_name=identity.name,
        )
        for hit in reach(graph, "requires")
        if hit.row.repo is not None and repo_key(hit.row.repo) in wanted_keys
    ]


__all__ = ["find_matches"]
