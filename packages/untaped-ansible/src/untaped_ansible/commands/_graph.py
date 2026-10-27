"""What the graph commands share: the source they read, the graph of one root, and its report.

``deps``, ``impact``, ``find`` and ``graph`` resolve their source data the
same way (:meth:`GraphCommand.env`): the selected sources, or the inline
selectors, or ``ansible.default_source``; nothing is refreshed until
:meth:`GraphCommand.answer` reads, so arguments are checked first. Each
root's graph is built by :meth:`GraphCommand.target_graph`, overlaying a local
checkout and reading downstream GitHub live when the source can't answer.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Iterable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import Field

from untaped.sdk import (
    NeverRefreshed,
    Parallel,
    Refresh,
    UsageError,
    key,
    not_found,
    plural,
    q,
)
from untaped_ansible.application.graph import BuildGraph, GraphRequest
from untaped_ansible.application.ports import DependencyIndex
from untaped_ansible.commands._ansible import (
    AnsibleCommand,
    inline_source_key,
    saved_source_key,
    source_definition,
)
from untaped_ansible.commands._source_data import SourceData
from untaped_ansible.domain.graph import DependencyGraph
from untaped_ansible.domain.identity import IdentityResolver
from untaped_ansible.domain.models import DependencyDeclaration, ParseWarning
from untaped_ansible.domain.parser import parse_dependency_file
from untaped_ansible.domain.payloads import IndexedDependency, SkippedDependencyFile
from untaped_ansible.errors import AnsibleError
from untaped_ansible.infrastructure import (
    GithubDependencyIndex,
    MultiSourceDependencyIndex,
    NullDependencyIndex,
    OverlayDependencyIndex,
    SqliteDependencyIndex,
    local_remote_url,
    open_index,
)
from untaped_ansible.refresh import (
    GIT_PARALLEL_CAP,
    Backend,
    SelectedSource,
    format_skipped_dependency_file,
    ignored_collections_warning,
    serve_sources,
)
from untaped_ansible.settings import (
    SOURCE_DATA,
    SOURCES,
    AnsibleSettings,
    SourceDefinition,
    alias_map,
)
from untaped_github.api import GithubClient, GithubSettings, github_token_found

GraphDirection = Literal["deps", "impact", "both"]
CommandName = Literal["graph", "deps", "impact", "find"]

TARGET = "Role or repo: owner/repo, GitHub URL, source alias, or local path."
TARGET_REPO = (
    "owner/repo of a local target; needed for a subdirectory of a checkout, whose remote "
    "is read only at the checkout's top level."
)
DEPTH = "Traversal depth, or 'unlimited' (default)."
ALL_REFS = (
    "With no --ref, read what every cached ref depends on, not only the default branch. "
    "Needs a source; not with --live."
)
LIVE = (
    "Read downstream dependencies live from GitHub even when a source is selected; "
    "not with --refresh or --cached."
)


class GraphCommand(AnsibleCommand):
    """A command that reads Ansible dependency graphs from a source's data or GitHub."""

    data: SourceData
    refresh: Refresh = Refresh(SOURCE_DATA)
    parallel: Parallel = Parallel(
        default=key(AnsibleSettings).git_fetch_parallel, cap=GIT_PARALLEL_CAP
    )
    backend: Backend | None = Field(
        None,
        description=(
            "Ref probe backend for a refresh this command runs: auto, graphql, or git; "
            "not with --cached."
        ),
    )
    depth: str | None = Field(None, description=DEPTH)

    # -- the source -------------------------------------------------------------

    def env(
        self, stack: ExitStack, *, command: CommandName, live: bool, all_refs: bool
    ) -> GraphEnv:
        """Resolve settings and the selected source into what every root's build shares.

        Nothing is refreshed yet (see :meth:`answer`), so arguments can be
        checked against it first.
        """
        settings = self.settings
        data = self.data
        if live and self.refresh.directive is not None:
            flag = "--refresh" if self.refresh.directive else "--cached"
            raise UsageError(f"--live reads GitHub live; drop {flag}")
        has_source = bool(
            data.source or data.org or data.team or data.repo or settings.default_source
        )
        flags = [
            flag
            for flag, given in (
                ("--refresh", self.refresh.directive is True),
                ("--cached", self.refresh.directive is False),
                ("--backend", self.backend is not None),
            )
            if given
        ]
        if flags and not has_source:
            raise UsageError(
                f"{flags[0]} needs a source; select one with --source NAME or inline selectors "
                "(or set ansible.default_source)"
            )
        if self.backend is not None and self.refresh.directive is False:
            raise UsageError("--backend needs a refresh; drop --cached")
        aliases = alias_map()
        github = self.github
        github_host = self.github_host
        graph_source = _graph_source(data, default_source=settings.default_source)
        # Live reads resolve only the default branch; every ref exists only in a source's cache.
        if all_refs and live:
            raise UsageError("--all-refs reads cached source data; drop --live")
        if all_refs and not graph_source.selections:
            raise UsageError(
                "--all-refs reads cached source data; select one with --source NAME "
                "(or set ansible.default_source)"
            )
        sqlite_index = open_index()
        index = _dependency_index_for_graph_source(sqlite_index, graph_source)
        return GraphEnv(
            command=command,
            settings=settings,
            aliases=aliases,
            github_settings=github,
            github_host=github_host,
            index=index,
            sqlite_index=sqlite_index,
            graph_source=graph_source,
            live=live,
            depth=parse_depth(self.depth),
            all_refs=all_refs,
            live_reads=_LiveReads(
                stack,
                open_client=lambda: GithubClient(github, http=self.ctx.http),
                wrapped=index,
                aliases=aliases,
                settings=settings,
                github_host=github_host,
            ),
        )

    def answer[T](
        self,
        env: GraphEnv,
        direction: GraphDirection,
        read: Callable[[], T],
        *,
        empty: Callable[[T], bool],
    ) -> T:
        """``read()``, over the selected sources, refreshed first when ``--refresh`` or age asks.

        A command that reads no source's data (none selected, or ``--live``
        reading downstream only) just reads.
        """
        if not _reads_source(env, direction):
            return read()
        wiring = self.wiring(env.sqlite_index, parallel=self.parallel.value, backend=self.backend)
        try:
            served = serve_sources(
                env.graph_source.selections,
                refresh=self.refresh.directive,
                read=read,
                empty=empty,
                wiring=wiring,
            )
        except NeverRefreshed as exc:
            if direction == "deps":
                exc.hint = (
                    "or pass --live to read downstream dependencies from GitHub without cached data"
                )
            raise
        return served.value

    # -- one root's graph ---------------------------------------------------------

    def target_graph(
        self,
        env: GraphEnv,
        *,
        target: str,
        ref: str | None,
        target_repo: str | None,
        direction: GraphDirection,
    ) -> BuiltGraph:
        """Build one root's graph, with its warnings attached, and say what it read."""
        target_repo_name = require_repo(env, target, target_repo=target_repo)
        graph_source = env.graph_source
        direction, graph_warnings = _effective_direction(graph_source, direction=direction)
        parse_warnings: list[str] = []

        target_path = Path(target).expanduser()
        local: _LocalDependencies | None = None
        if target_path.exists():
            local = _local_dependencies(
                target_path,
                repo=target_repo_name,
                ref=ref,
                aliases=env.aliases,
                dependency_paths=env.settings.dependency_paths,
                github_host=env.github_host,
            )
            parse_warnings.extend(local.warnings)

        graph, read_warnings, reads = _graph_for_target(
            env.index,
            request=GraphRequest(
                repo=target_repo_name,
                ref=ref,
                source_key=graph_source.key,
                direction=direction,
                depth=env.depth,
                # A local checkout is one state of its repo, overlaid at the ref-less node.
                all_refs=env.all_refs or local is not None,
                refresh_hint=_refresh_hint(graph_source),
            ),
            local=local,
            use_live=_should_use_live_dependencies(
                direction=direction, source_key=graph_source.key, live=env.live
            ),
            live_reads=env.live_reads,
        )
        parse_warnings.extend(read_warnings)
        graph = _with_graph_warnings(
            graph,
            [
                *graph_warnings,
                *parse_warnings,
                *_empty_graph_warnings(
                    graph,
                    direction=direction,
                    dependency_paths=env.settings.dependency_paths,
                    source_label=graph_source.label,
                ),
            ],
        )
        parts = ["local checkout"] if local is not None else []
        if reads.source and graph_source.header:
            parts.append(graph_source.header)
        if reads.live:
            parts.append("downstream live" if reads.source else "live reads")
        return BuiltGraph(graph, ", ".join(parts))

    # -- what it says on stderr ---------------------------------------------------

    def report_warnings(self, warnings: Iterable[str]) -> None:
        for warning in warnings:
            self.ctx.diag.warning(warning)

    def report_depth_stops(
        self, graph: DependencyGraph, depth: int | None, *, root: str = ""
    ) -> None:
        """Hint on stderr how many nodes the depth limit left unread, and how to read them."""
        count = sum(1 for node in graph.nodes if node.stopped == "depth")
        if depth is None or not count:
            return
        prefix = f"{root}: " if root else ""
        them = "it" if count == 1 else "them"
        self.ctx.diag.hint(
            f"{prefix}{plural(count, 'repo')} not read beyond --depth {depth}; "
            f"pass --depth unlimited to read {them}"
        )


@dataclass(frozen=True)
class GraphEnv:
    """Everything a single root's graph build shares with the others."""

    command: CommandName
    settings: AnsibleSettings
    aliases: dict[str, str]
    github_settings: GithubSettings
    github_host: str | None
    index: DependencyIndex
    sqlite_index: SqliteDependencyIndex
    graph_source: GraphSource
    live: bool
    depth: int | None
    all_refs: bool
    live_reads: _LiveReads


class BuiltGraph(NamedTuple):
    graph: DependencyGraph
    data_source: str
    """What the build read, for the tree header: ``source prod, downstream live``."""


@dataclass(frozen=True)
class GraphSource:
    selections: tuple[SelectedSource, ...]
    key: str | None
    label: str | None
    saved: bool

    @property
    def header(self) -> str | None:
        """How the tree header names it: ``source prod``, ``sources a, b``, ``inline source K``."""
        if self.saved and len(self.selections) == 1:
            return f"source {self.label}"
        return self.label


class _LiveReads:
    """One live GitHub read index shared by every root of a graph command.

    It opens the GitHub client on first use, so each repo/ref is read live
    once per command however many ``find`` roots reach it.
    """

    def __init__(
        self,
        stack: ExitStack,
        *,
        open_client: Callable[[], GithubClient],
        wrapped: DependencyIndex,
        aliases: dict[str, str],
        settings: AnsibleSettings,
        github_host: str | None,
    ) -> None:
        self._stack = stack
        self._open_client = open_client
        self._wrapped = wrapped
        self._aliases = aliases
        self._settings = settings
        self._github_host = github_host
        self._index: GithubDependencyIndex | None = None

    def index(self) -> GithubDependencyIndex:
        if self._index is None:
            github = self._stack.enter_context(self._open_client())
            self._index = GithubDependencyIndex(
                github=github,
                wrapped=self._wrapped,
                aliases=self._aliases,
                dependency_paths=self._settings.dependency_paths,
                github_host=self._github_host,
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
class _LocalDependencies:
    edges: list[IndexedDependency]
    warnings: list[str]


def parse_depth(value: str | None) -> int | None:
    """``--depth``: a number of hops from 0, or ``unlimited`` (``None``, also when unset)."""
    if value is None or value == "unlimited":
        return None
    try:
        depth = int(value)
    except ValueError:
        raise UsageError("--depth must be an integer or 'unlimited'") from None
    if depth < 0:
        raise UsageError("--depth must be >= 0")
    return depth


def within(depth: int | None) -> str:
    return "" if depth is None else f" within --depth {depth}"


def check_all_refs(ref: str | None, *, all_refs: bool) -> None:
    if ref is not None and all_refs:
        raise UsageError("--all-refs reads every ref; drop it or --ref")


def require_repo(env: GraphEnv, target: str, *, target_repo: str | None = None) -> str:
    """The ``owner/repo`` TARGET names, or an error saying why there is none."""
    repo = target_repo or _resolve_target_repo(target, env.aliases, github_host=env.github_host)
    if repo is not None:
        return repo
    message = f"could not resolve target to a GitHub repo: {q(target)}"
    if Path(target).expanduser().exists():
        message = (
            f"{message}; the local path is not the top level of a Git checkout "
            "with a remote pointing at GitHub"
        )
        if env.command != "find":
            message = f"{message}. Pass --target-repo OWNER/NAME"
    raise AnsibleError(message, category="not_found")


def _reads_source(env: GraphEnv, direction: GraphDirection) -> bool:
    """Whether a build in ``direction`` reads the selected sources' data.

    Only ``deps --live`` reads nothing of them: ``graph --live`` still reads
    its upstream half (``impact``) from the index.
    """
    if not env.graph_source.selections:
        return False
    return not (env.live and direction == "deps")


def _graph_source(data: SourceData, *, default_source: str | None) -> GraphSource:
    """The explicit selection, else ``ansible.default_source``, else no source."""
    names = list(dict.fromkeys(data.source))
    if names and data.inline:
        raise UsageError(
            "--source cannot be combined with --org, --team, --repo, --path, "
            "--ref-kind, --ref-pattern, or --ref-scan-default"
        )
    from_default = False
    if not names and not data.inline and default_source is not None:
        names, from_default = [default_source], True
    if names:
        selections: list[SelectedSource] = []
        for name in names:
            source = SOURCES.get(name)
            if source is None:
                known = sorted(entry.name for entry in SOURCES.all())
                message = not_found("source", name, known=known)
                if from_default:
                    # The user's own setting names a missing source: fix the config.
                    message = f"{message} (set by ansible.default_source)"
                raise AnsibleError(
                    message, category="config" if from_default else "not_found", system="local"
                )
            selections.append(
                SelectedSource(
                    definition=source,
                    key=saved_source_key(name),
                    label=name,
                    subject=f"source {name}",
                )
            )
        return GraphSource(
            selections=tuple(selections),
            key=_graph_source_key(selections),
            label=_graph_source_label(selections),
            saved=True,
        )
    if data.inline:
        source = source_definition(
            name="<inline>",
            orgs=data.org,
            teams=data.team,
            repos=data.repo,
            paths=data.path,
            ref_kinds=data.ref_kind,
            ref_patterns=data.ref_pattern,
            ref_scan_default=data.ref_scan_default,
        )
        key = inline_source_key(source)
        label = _inline_label(source)
        return GraphSource(
            selections=(SelectedSource(definition=source, key=key, label=label, subject=label),),
            key=key,
            label=label,
            saved=False,
        )
    return GraphSource(selections=(), key=None, label=None, saved=False)


def _inline_label(source: SourceDefinition) -> str:
    """``inline source --org acme --repo acme/x``: the selector flags, in help order."""
    flags: list[str] = []
    for flag, values in (
        ("--org", source.orgs),
        ("--team", source.teams),
        ("--repo", source.repos),
        ("--path", source.dependency_paths),
        ("--ref-kind", source.ref_kinds),
        ("--ref-pattern", source.ref_patterns),
        ("--ref-scan-default", [source.ref_scan_default] if source.ref_scan_default else []),
    ):
        for value in values:
            flags += [flag, value]
    return f"inline source {shlex.join(flags)}"


def _graph_source_key(selections: list[SelectedSource]) -> str:
    if len(selections) == 1:
        return selections[0].key
    names = ",".join(selection.key.removeprefix("source:") for selection in selections)
    return f"sources:{names}"


def _graph_source_label(selections: list[SelectedSource]) -> str:
    if len(selections) == 1:
        return selections[0].label
    return f"sources {', '.join(selection.label for selection in selections)}"


def _dependency_index_for_graph_source(
    index: DependencyIndex, source_state: GraphSource
) -> DependencyIndex:
    if len(source_state.selections) <= 1:
        return index
    return MultiSourceDependencyIndex(
        index, tuple(selection.key for selection in source_state.selections)
    )


def _effective_direction(
    source_state: GraphSource, *, direction: GraphDirection
) -> tuple[GraphDirection, list[str]]:
    """``direction``, downstream only (with a warning) when no source can answer upstream."""
    if source_state.selections or direction == "deps":
        return direction, []
    message = (
        "upstream requires --source NAME, inline selectors like --org, --team, or --repo, "
        "or ansible.default_source"
    )
    if direction == "impact":
        raise UsageError(message)
    return "deps", [
        "only showing downstream; upstream omitted because no source is configured. "
        "Pass --source NAME or inline selectors, or set ansible.default_source."
    ]


def _resolve_target_repo(
    target: str, aliases: dict[str, str], *, github_host: str | None
) -> str | None:
    path = Path(target).expanduser()
    if path.exists():
        return _repo_from_local_git(path, github_host=github_host)
    declaration = DependencyDeclaration(name=target, src=target, source_path="<target>")
    return IdentityResolver(aliases, github_host=github_host).resolve(declaration).repo


def _repo_from_local_git(path: Path, *, github_host: str | None) -> str | None:
    origin_url = local_remote_url(path)
    if origin_url is None:
        return None
    declaration = DependencyDeclaration(name=origin_url, src=origin_url, source_path="<git-remote>")
    return IdentityResolver(github_host=github_host).resolve(declaration).repo


def _local_dependencies(
    path: Path,
    *,
    repo: str,
    ref: str | None,
    aliases: dict[str, str],
    dependency_paths: list[str],
    github_host: str | None,
) -> _LocalDependencies:
    edges: list[IndexedDependency] = []
    warnings: list[str] = []
    ignored_collections: list[str] = []
    resolver = IdentityResolver(aliases, github_host=github_host)
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
    return _LocalDependencies(edges=edges, warnings=warnings)


def _parse_warning_messages(warnings: Iterable[ParseWarning]) -> list[str]:
    return [f"skipped {warning.source_path}: {warning.reason}" for warning in warnings]


def _live_parse_warning_messages(warnings: Iterable[SkippedDependencyFile]) -> list[str]:
    return [format_skipped_dependency_file(warning) for warning in warnings]


def _graph_for_target(
    index: DependencyIndex,
    *,
    request: GraphRequest,
    local: _LocalDependencies | None,
    use_live: bool,
    live_reads: _LiveReads,
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
            warnings.append(
                "transitive dependencies were not expanded: pass --source NAME to use "
                "cached source data, or run `untaped auth login github` for live GitHub reads"
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


def _refresh_hint(source_state: GraphSource) -> str | None:
    """Compose the exact fix command surfaced in missing-ref warnings."""
    if not source_state.selections:
        return None
    if source_state.saved:
        commands = " and ".join(
            f"`untaped ansible source refresh {selection.label}`"
            for selection in source_state.selections
        )
        return f"Run {commands} to update it."
    return "Re-run this command with `--refresh` to update it."


def _with_graph_warnings(graph: DependencyGraph, warnings: list[str]) -> DependencyGraph:
    if not warnings:
        return graph
    return graph.model_copy(update={"warnings": tuple(dict.fromkeys((*graph.warnings, *warnings)))})


def _empty_graph_warnings(
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


def _should_use_live_dependencies(
    *, direction: GraphDirection, source_key: str | None, live: bool
) -> bool:
    if direction == "impact":
        return False
    if source_key is None:
        return True
    return live
