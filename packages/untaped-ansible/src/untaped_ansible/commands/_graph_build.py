"""How a graph command builds one root's graph.

:func:`graph_for_target` reads the cached source, overlays a local checkout
(:func:`local_dependencies`) and reads downstream live from each repo's
provider, through the repo store, with the command's one :class:`LiveReads`
when no source answers; it returns its warnings beside the graph, and
:func:`with_graph_warnings` attaches them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from untaped_ansible.application.graph import BuildGraph, GraphDirection, GraphRequest
from untaped_ansible.application.ports import DependencyIndex
from untaped_ansible.domain.graph import DependencyGraph
from untaped_ansible.domain.identity import IdentityResolver
from untaped_ansible.domain.models import DependencyDeclaration, ParseWarning
from untaped_ansible.domain.parser import parse_dependency_file
from untaped_ansible.domain.payloads import IndexedDependency, SkippedDependencyFile
from untaped_ansible.infrastructure import OverlayDependencyIndex, local_remote_url
from untaped_ansible.live_reads import LiveReads
from untaped_ansible.refresh import format_skipped_dependency_file, ignored_collections_warning


class _Reads(NamedTuple):
    """What a graph build read besides a local checkout."""

    source: bool
    """The cached source data."""
    live: bool
    """Downstream dependencies from each repo's provider."""


@dataclass(frozen=True)
class LocalDependencies:
    edges: list[IndexedDependency]
    warnings: list[str]


def resolve_target_repo(target: str, resolver: IdentityResolver) -> str | None:
    """The repository TARGET names: by alias, name or URL, or by a local checkout's origin URL."""
    path = Path(target).expanduser()
    if path.exists():
        origin_url = local_remote_url(path)
        return None if origin_url is None else resolver.repo_of(origin_url)
    declaration = DependencyDeclaration(name=target, src=target, source_path="<target>")
    return resolver.resolve(declaration).repo


def local_dependencies(
    path: Path,
    *,
    repo: str,
    ref: str | None,
    resolver: IdentityResolver,
    dependency_paths: list[str],
) -> LocalDependencies:
    edges: list[IndexedDependency] = []
    warnings: list[str] = []
    ignored_collections: list[str] = []
    for relative in dependency_paths:
        dep_path = path / relative
        if not dep_path.is_file():
            continue
        report = parse_dependency_file(relative, dep_path.read_text())
        warnings.extend(_parse_warning_messages(report.warnings))
        ignored_collections.extend(report.ignored_collections)
        for declaration in report.dependencies:
            resolved = resolver.resolve(declaration)
            edges.append(
                IndexedDependency(
                    source_repo=repo,
                    source_ref=ref,
                    dependency_repo=resolved.repo,
                    dependency_name=declaration.name,
                    dependency_version=declaration.version,
                    source_path=relative,
                    unresolved=resolved.unresolved,
                )
            )
    collections_warning = ignored_collections_warning(ignored_collections)
    if collections_warning is not None:
        warnings.append(collections_warning)
    return LocalDependencies(edges=edges, warnings=warnings)


def _parse_warning_messages(warnings: Iterable[ParseWarning]) -> list[str]:
    return [f"skipped {warning.source_path}: {warning.reason}" for warning in warnings]


def _live_parse_warning_messages(warnings: Iterable[SkippedDependencyFile]) -> list[str]:
    return [format_skipped_dependency_file(warning) for warning in warnings]


def graph_for_target(
    index: DependencyIndex,
    *,
    request: GraphRequest,
    local: LocalDependencies | None,
    use_live: bool,
    live_reads: LiveReads,
) -> tuple[DependencyGraph, list[str], _Reads]:
    """Build the graph, reading transitive dependencies live when no source answers.

    Local target edges always overlay the chosen read index. Without a
    source there is no cache to scope reads to, so local and remote targets
    alike read transitive dependencies live, from each repo's provider.
    """

    def build(read_index: DependencyIndex) -> DependencyGraph:
        if local is not None:
            read_index = OverlayDependencyIndex(
                read_index, local.edges, authoritative_sources={(request.repo, request.ref)}
            )
        return BuildGraph(read_index)(request.model_copy(update={"live": use_live}))

    if not use_live:
        return build(index), [], _Reads(source=request.source_key is not None, live=False)
    live_index = live_reads.index()
    # The index is shared across roots: report only what this build read.
    seen_errors, seen_warnings = len(live_index.errors), len(live_index.warnings)
    graph = build(live_index)
    return (
        graph,
        [
            *live_index.errors[seen_errors:],
            *_live_parse_warning_messages(live_index.warnings[seen_warnings:]),
        ],
        _Reads(source=False, live=True),
    )


def with_graph_warnings(graph: DependencyGraph, warnings: list[str]) -> DependencyGraph:
    if not warnings:
        return graph
    return graph.model_copy(update={"warnings": tuple(dict.fromkeys((*graph.warnings, *warnings)))})


def empty_graph_warnings(
    graph: DependencyGraph,
    *,
    direction: GraphDirection,
    dependency_paths: list[str],
    source_label: str | None,
) -> list[str]:
    if graph.edges:
        return []
    paths = ", ".join(dependency_paths)
    if direction == "deps":
        return [
            f"no declared downstream dependencies found for {graph.target_id}; "
            f"checked configured dependency paths: {paths}"
        ]
    label = source_label or "source"
    if direction == "impact":
        return [f"no cached upstream dependents found for {graph.target_id} in {label}"]
    return [
        f"no declared downstream dependencies or cached upstream dependents found for "
        f"{graph.target_id} in {label}; checked configured dependency paths: {paths}"
    ]


def should_use_live_dependencies(*, direction: GraphDirection, source_key: str | None) -> bool:
    """Whether a build reads downstream live: only with no source, never upstream."""
    return direction != "impact" and source_key is None
