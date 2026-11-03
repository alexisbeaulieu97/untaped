"""How a graph command builds one root's graph.

:func:`graph_for_target` reads the cached source, overlays a local checkout
(:func:`local_dependencies`) and reads downstream GitHub live through the
command's one :class:`LiveReads` when the source can't answer; it returns
its warnings beside the graph, and :func:`with_graph_warnings` attaches them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from untaped.sdk import run_hint
from untaped_ansible.application.graph import BuildGraph, GraphDirection, GraphRequest
from untaped_ansible.application.ports import DependencyIndex
from untaped_ansible.domain.graph import DependencyGraph
from untaped_ansible.domain.identity import IdentityResolver
from untaped_ansible.domain.models import DependencyDeclaration, ParseWarning
from untaped_ansible.domain.parser import parse_dependency_file
from untaped_ansible.domain.payloads import IndexedDependency, SkippedDependencyFile
from untaped_ansible.infrastructure import (
    GithubDependencyIndex,
    NullDependencyIndex,
    OverlayDependencyIndex,
    local_remote_url,
)
from untaped_ansible.refresh import format_skipped_dependency_file, ignored_collections_warning
from untaped_ansible.settings import AnsibleSettings
from untaped_github.api import GithubApi, github_token_found


class LiveReads:
    """One live GitHub read index shared by every root of a graph command.

    It opens github's port on first use, so a command that reads nothing live
    needs no token, and each repo/ref is read live once per command however
    many ``find`` roots reach it.
    """

    def __init__(
        self,
        open_github: Callable[[], GithubApi],
        *,
        wrapped: DependencyIndex,
        resolver: Callable[[], IdentityResolver],
        settings: AnsibleSettings,
    ) -> None:
        self._open_github = open_github
        self._wrapped = wrapped
        self._resolver = resolver
        self._settings = settings
        self._index: GithubDependencyIndex | None = None

    def index(self) -> GithubDependencyIndex:
        if self._index is None:
            self._index = GithubDependencyIndex(
                github=self._open_github(),
                wrapped=self._wrapped,
                resolver=self._resolver(),
                dependency_paths=self._settings.dependency_paths,
                concurrency=self._settings.probe_parallel,
            )
        return self._index


class _Reads(NamedTuple):
    """What a graph build read besides a local checkout."""

    source: bool
    """The cached source data."""
    live: bool
    """Downstream dependencies from GitHub."""


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
    """Build the graph, reading transitive dependencies live when requested.

    Local target edges always overlay the chosen read index. Without a
    source there is no cache to scope reads to, so local and remote targets
    alike read transitive dependencies live; a local target without a
    GitHub token stays offline and only shows its own declarations.
    """

    def build(read_index: DependencyIndex) -> DependencyGraph:
        if local is not None:
            read_index = OverlayDependencyIndex(
                read_index, local.edges, authoritative_sources={(request.repo, request.ref)}
            )
        return BuildGraph(read_index)(request.model_copy(update={"live": use_live}))

    if not use_live:
        return build(index), [], _Reads(source=request.source_key is not None, live=False)
    if local is not None and request.source_key is None and not github_token_found():
        warnings = []
        if any(edge.dependency_repo is not None for edge in local.edges):
            from untaped.core.api import Login  # noqa: PLC0415 - core's commands load only here

            warnings.append(
                "transitive dependencies were not expanded: pass --source NAME to use "
                f"cached source data, or {run_hint(Login.spelled('github'))} for live GitHub reads"
            )
        return build(NullDependencyIndex()), warnings, _Reads(source=False, live=False)
    live_index = live_reads.index()
    # The index is shared across roots: report only what this build read.
    seen_errors, seen_warnings = len(live_index.errors), len(live_index.warnings)
    graph = build(live_index)
    # The live index falls back to the cached source for the upstream half.
    reads = _Reads(source=request.source_key is not None and request.direction != "deps", live=True)
    return (
        graph,
        [
            *live_index.errors[seen_errors:],
            *_live_parse_warning_messages(live_index.warnings[seen_warnings:]),
        ],
        reads,
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


def should_use_live_dependencies(
    *, direction: GraphDirection, source_key: str | None, live: bool
) -> bool:
    if direction == "impact":
        return False
    if source_key is None:
        return True
    return live
