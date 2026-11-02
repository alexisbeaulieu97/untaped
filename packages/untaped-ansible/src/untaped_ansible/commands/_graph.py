"""What the graph commands share: the source they read, the graph of one root, and its report.

``deps``, ``impact``, ``find`` and ``graph`` resolve their source data the
same way (:meth:`GraphCommand.env`): the selected sources, or the inline
selectors, or ``ansible.default_source``; nothing is refreshed until
:meth:`GraphCommand.answer` reads, so arguments are checked first. Each
root's graph is built by :meth:`GraphCommand.target_graph`, overlaying a local
checkout and reading downstream GitHub live when the source can't answer.
Which source a command reads lives in
:mod:`untaped_ansible.commands._graph_source`, and how one root's graph is
built in :mod:`untaped_ansible.commands._graph_build`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import Field

from untaped.sdk import NeverRefreshed, Parallel, Refresh, UsageError, key, plural, q
from untaped_ansible.application.graph import GraphDirection, GraphRequest
from untaped_ansible.application.ports import DependencyIndex
from untaped_ansible.commands._ansible import AnsibleCommand
from untaped_ansible.commands._graph_build import (
    LiveReads,
    LocalDependencies,
    empty_graph_warnings,
    graph_for_target,
    local_dependencies,
    resolve_target_repo,
    should_use_live_dependencies,
    with_graph_warnings,
)
from untaped_ansible.commands._graph_source import (
    GraphSource,
    dependency_index_for_graph_source,
    effective_direction,
    graph_source_of,
    refresh_hint,
)
from untaped_ansible.commands._source_data import SourceData
from untaped_ansible.domain.graph import DependencyGraph
from untaped_ansible.errors import AnsibleError
from untaped_ansible.infrastructure import SqliteDependencyIndex, open_index
from untaped_ansible.refresh import GIT_PARALLEL_CAP, Backend, serve_sources
from untaped_ansible.settings import SOURCE_DATA, AnsibleSettings, alias_map
from untaped_github.api import GithubClient, GithubSettings

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
        graph_source = graph_source_of(data, default_source=settings.default_source)
        # Live reads resolve only the default branch; every ref exists only in a source's cache.
        if all_refs and live:
            raise UsageError("--all-refs reads cached source data; drop --live")
        if all_refs and not graph_source.selections:
            raise UsageError(
                "--all-refs reads cached source data; select one with --source NAME "
                "(or set ansible.default_source)"
            )
        sqlite_index = open_index()
        index = dependency_index_for_graph_source(sqlite_index, graph_source)
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
            live_reads=LiveReads(
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
        direction, graph_warnings = effective_direction(graph_source, direction=direction)
        parse_warnings: list[str] = []

        target_path = Path(target).expanduser()
        local: LocalDependencies | None = None
        if target_path.exists():
            local = local_dependencies(
                target_path,
                repo=target_repo_name,
                ref=ref,
                aliases=env.aliases,
                dependency_paths=env.settings.dependency_paths,
                github_host=env.github_host,
            )
            parse_warnings.extend(local.warnings)

        graph, read_warnings, reads = graph_for_target(
            env.index,
            request=GraphRequest(
                repo=target_repo_name,
                ref=ref,
                source_key=graph_source.key,
                direction=direction,
                depth=env.depth,
                # A local checkout is one state of its repo, overlaid at the ref-less node.
                all_refs=env.all_refs or local is not None,
                refresh_hint=refresh_hint(graph_source),
            ),
            local=local,
            use_live=should_use_live_dependencies(
                direction=direction, source_key=graph_source.key, live=env.live
            ),
            live_reads=env.live_reads,
        )
        parse_warnings.extend(read_warnings)
        graph = with_graph_warnings(
            graph,
            [
                *graph_warnings,
                *parse_warnings,
                *empty_graph_warnings(
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
    live_reads: LiveReads


class BuiltGraph(NamedTuple):
    graph: DependencyGraph
    data_source: str
    """What the build read, for the tree header: ``source prod, downstream live``."""


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
    repo = target_repo or resolve_target_repo(target, env.aliases, github_host=env.github_host)
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
