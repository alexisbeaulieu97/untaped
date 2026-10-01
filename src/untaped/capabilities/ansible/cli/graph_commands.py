"""Dependency-graph commands (deps, impact, find, graph) and their CLI helpers.

``deps``, ``impact`` and ``find`` answer one question each and print rows
(``ansible.dependency``, ``ansible.dependent``, ``ansible.dependency_match``);
``graph`` renders the whole graph as a tree, Mermaid or JSON document, both
directions at once by default. All four report warnings on stderr, share the
source-data flags of :class:`GraphSourceOptions` and fall back to
``ansible.default_source`` when no source is selected.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, NamedTuple

from cyclopts import App, Group, Parameter, validators

import untaped.capabilities.ansible.cli.source_commands as source_commands
from untaped.capabilities.ansible.application.graph import BuildGraph, GraphRequest
from untaped.capabilities.ansible.application.ports import DependencyIndex
from untaped.capabilities.ansible.application.refresh_git_index import RefreshResult
from untaped.capabilities.ansible.cli.graph_tree import print_tree, tree_glyphs
from untaped.capabilities.ansible.cli.refresh import (
    GIT_PARALLEL_CAP,
    format_skipped_dependency_file,
    ignored_collections_warning,
    run_source_refresh,
)
from untaped.capabilities.ansible.domain.containment import DependencyMatch, find_matches
from untaped.capabilities.ansible.domain.graph import DependencyGraph, EdgeRelation
from untaped.capabilities.ansible.domain.graph_roots import (
    REPO_FIELDS,
    GraphRoot,
    RootInput,
    input_from_record,
    root_from_line,
    root_from_record,
)
from untaped.capabilities.ansible.domain.identity import IdentityResolver, repo_key
from untaped.capabilities.ansible.domain.models import DependencyDeclaration, ParseWarning
from untaped.capabilities.ansible.domain.parser import parse_dependency_file
from untaped.capabilities.ansible.domain.payloads import IndexedDependency, SkippedDependencyFile
from untaped.capabilities.ansible.domain.reach import ReachedNode, reach
from untaped.capabilities.ansible.domain.renderers import (
    GraphFormat,
    plain_text,
    render_graph,
    tree_lines,
)
from untaped.capabilities.ansible.errors import AnsibleError
from untaped.capabilities.ansible.infrastructure import (
    AliasRepository,
    GithubDependencyIndex,
    MultiSourceDependencyIndex,
    NullDependencyIndex,
    OverlayDependencyIndex,
    SourceRepository,
    SqliteDependencyIndex,
    local_remote_url,
)
from untaped.capabilities.ansible.settings import AnsibleSettings, SourceDefinition
from untaped.capabilities.github.api import GithubClient, GithubSettings, github_web_host
from untaped.capabilities.github.api import github_settings as load_github_settings
from untaped.sdk import (
    ColumnsOption,
    FormatOption,
    HttpSettings,
    ParallelOption,
    UiContext,
    UsageError,
    app_context,
    clamp_parallel,
    deprecated_alias,
    echo,
    emit,
    get_config_section,
    not_found,
    plural,
    q,
    raise_usage,
    read_stdin_input,
    report_errors,
)

GraphDirection = Literal["deps", "impact", "both"]
GraphCommand = Literal["graph", "deps", "impact", "find"]
BackendOption = Annotated[
    Literal["auto", "graphql", "git"] | None,
    Parameter(
        name="--backend",
        help="Ref probe backend for source refresh: auto, graphql, or git.",
    ),
]
RefOption = Annotated[
    str | None,
    Parameter(
        name="--ref",
        help="Target branch, tag, or SHA; omit for its default branch (and every ref upstream).",
    ),
]
TargetRepoOption = Annotated[
    str | None,
    Parameter(name="--target-repo", help="Canonical owner/repo override for local targets."),
]
_TARGET_HELP = "Role or repo: owner/repo, GitHub URL, source alias, or local path."
RoleArgument = Annotated[str, Parameter(help=_TARGET_HELP)]
DepthOption = Annotated[
    str | None, Parameter(name="--depth", help="Traversal depth, or 'unlimited' (default).")
]

AllRefsOption = Annotated[
    bool,
    Parameter(
        name="--all-refs",
        negative="",
        help="With no --ref, read what every cached ref depends on, not only the default "
        "branch. Needs a source; not with --live.",
    ),
]

# LimitedChoice() defaults to at-most-one selection — cyclopts' MutuallyExclusive
# is an untyped alias for exactly this, so the typed parent is used directly.
_SOURCE_DATA_GROUP = Group("Source Data", validator=validators.LimitedChoice())
LiveOption = Annotated[
    bool,
    Parameter(
        name="--live",
        negative="",
        group=_SOURCE_DATA_GROUP,
        help="Read downstream dependencies live from GitHub even when a source is selected.",
    ),
]


@Parameter(name="*")
@dataclass(frozen=True, kw_only=True)
class GraphSourceOptions:
    """Source-data flags shared by every graph command, in their help order.

    ``--live`` is not among them: ``impact`` reads cached source data only.
    """

    source: Annotated[
        list[str] | None,
        Parameter(
            name="--source",
            help=(
                "Saved source to read cached graph data from; repeat to union. "
                "Defaults to ansible.default_source."
            ),
            consume_multiple=False,
            negative="",
        ),
    ] = None
    refresh: Annotated[
        bool,
        Parameter(
            name="--refresh",
            negative="",
            group=_SOURCE_DATA_GROUP,
            help="Refresh source data before reading it.",
        ),
    ] = False
    parallel: ParallelOption | None = None
    backend: BackendOption = None
    orgs: Annotated[
        list[str] | None,
        Parameter(
            name="--org", help="Inline source GitHub org.", consume_multiple=False, negative=""
        ),
    ] = None
    teams: Annotated[
        list[str] | None,
        Parameter(
            name="--team",
            help=(
                "Inline source GitHub team as ORG/SLUG; a bare SLUG is allowed when "
                "exactly one --org is given and normalizes to ORG/SLUG."
            ),
            consume_multiple=False,
            negative="",
        ),
    ] = None
    repos: Annotated[
        list[str] | None,
        Parameter(
            name="--repo",
            help="Inline source GitHub repo as owner/name.",
            consume_multiple=False,
            negative="",
        ),
    ] = None
    paths: Annotated[
        list[str] | None,
        Parameter(
            name="--path",
            help="Inline source dependency path.",
            consume_multiple=False,
            negative="",
        ),
    ] = None
    ref_kinds: Annotated[
        list[str] | None,
        Parameter(
            name="--ref-kind",
            help="Inline source ref namespace to scan: heads or tags; omit for configured default.",
            consume_multiple=False,
            negative="",
        ),
    ] = None
    ref_patterns: Annotated[
        list[str] | None,
        Parameter(
            name="--ref-pattern",
            help="Inline source fnmatch pattern for branch/tag names; omit for configured default.",
            consume_multiple=False,
            negative="",
        ),
    ] = None
    ref_scan_default: Annotated[
        Literal["all", "default_branch"] | None,
        Parameter(
            name="--ref-scan-default",
            help="Inline source scan strategy: all refs or only each repo's default branch.",
        ),
    ] = None

    @property
    def has_inline(self) -> bool:
        """Whether any inline source selector or modifier is given."""
        return any(
            (
                self.orgs,
                self.teams,
                self.repos,
                self.paths,
                self.ref_kinds,
                self.ref_patterns,
                self.ref_scan_default,
            )
        )


_SOURCE_DEFAULTS = GraphSourceOptions()


def register_graph_commands(app: App) -> None:
    """Register the dependency-graph commands on the Ansible root app."""
    app.command(deps_command, name="deps")
    app.command(impact_command, name="impact")
    app.command(find_command, name="find")
    app.command(graph_command, name="graph")
    for old, direction in (("--upstream", "up"), ("--downstream", "down"), ("--both", "both")):
        deprecated_alias(app["graph"], old, f"--direction={direction}")


def deps_command(
    role: RoleArgument,
    /,
    *,
    ref: Annotated[
        str | None,
        Parameter(
            name="--ref",
            help="Branch, tag, or SHA of ROLE; omit for its default branch.",
        ),
    ] = None,
    all_refs: AllRefsOption = False,
    target_repo: TargetRepoOption = None,
    depth: DepthOption = None,
    live: LiveOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    options: GraphSourceOptions = _SOURCE_DEFAULTS,
) -> None:
    """Show what ROLE depends on (downstream), one row per repository per ROLE ref.

    Reads cached data when a source is selected (--source, inline selectors,
    or ansible.default_source) and GitHub live otherwise (or with --live).
    Each row carries the ref as declared, the shortest path from ROLE and
    the ROLE ref it was reached from.

    For example:

        untaped ansible deps acme/web --ref v2.1.0
        untaped ansible deps ./roles/web --target-repo acme/web --format json
    """
    _emit_reach(
        role,
        command="deps",
        ref=ref,
        all_refs=all_refs,
        target_repo=target_repo,
        depth=depth,
        live=live,
        fmt=fmt,
        columns=columns,
        options=options,
    )


def impact_command(
    role: RoleArgument,
    /,
    *,
    ref: Annotated[
        str | None,
        Parameter(
            name="--ref",
            help="Branch, tag, or SHA of ROLE to find dependents of; omit for every cached ref.",
        ),
    ] = None,
    target_repo: TargetRepoOption = None,
    depth: DepthOption = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    options: GraphSourceOptions = _SOURCE_DEFAULTS,
) -> None:
    """Show what depends on ROLE (upstream), one row per repository per root ref.

    Reads cached source data (--source, inline selectors, or
    ansible.default_source). Each row carries the ref it declares, its
    shortest path to ROLE and the ROLE ref it was reached from.

    For example:

        untaped ansible impact acme/base --source platform
        untaped ansible impact acme/base --ref main --format pipe
    """
    _emit_reach(
        role,
        command="impact",
        ref=ref,
        all_refs=False,
        target_repo=target_repo,
        depth=depth,
        live=False,
        fmt=fmt,
        columns=columns,
        options=options,
    )


def find_command(
    target: Annotated[
        list[str],
        Parameter(
            help="Repository to find: owner/repo, GitHub URL, or source alias; give several "
            "to find any of them."
        ),
    ],
    /,
    *,
    roots: Annotated[
        list[str] | None,
        Parameter(
            name="--root",
            help="Root to search as owner/repo[@ref], GitHub URL, source alias, or local path.",
            consume_multiple=False,
            negative="",
        ),
    ] = None,
    stdin: Annotated[
        bool,
        Parameter(
            name="--stdin",
            negative="",
            help=(
                "Read roots from stdin: owner/repo@ref lines, or pipe records with "
                "scm_url/repo_url/repo/full_name and effective_scm_ref/ref."
            ),
        ),
    ] = False,
    all_refs: AllRefsOption = False,
    depth: DepthOption = None,
    live: LiveOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    options: GraphSourceOptions = _SOURCE_DEFAULTS,
) -> None:
    """Find which roots contain a repository downstream, one row per match.

    Each row names the root, the matched repo, the ref exactly as declared,
    the dependency file and the shortest path; a root read from a pipe
    record also carries that record's kind, id and name.

    For example:

        untaped ansible find acme/base --root acme/site@main --root acme/app
        untaped awx job-templates list --with-scm --format pipe \\
          | untaped ansible find acme/base --stdin --format pipe
    """
    if stdin and roots:
        raise_usage("pass --root or --stdin, not both")
    if not stdin and not roots:
        raise_usage("provide --root or --stdin")
    depth_limit = _parse_depth(depth or "unlimited")
    with report_errors(), ExitStack() as stack:
        inputs = (
            _read_roots()
            if stdin
            else list(dict.fromkeys((root_from_line(r), RootInput()) for r in roots or []))
        )
        env = _graph_env(
            stack, options, command="find", depth=depth_limit, live=live, all_refs=all_refs
        )
        wanted = [_require_repo(env, repo) for repo in target]
        ui = _report_warnings(_refresh_selected(env, options))
        # Spellings of one repo (URL, .git, SSH, alias) share one graph build.
        graphs: dict[tuple[str, str | None, str | None], DependencyGraph] = {}
        matches: list[DependencyMatch] = []
        for root, source in inputs:
            repo = _require_repo(env, root.target)
            local = root.target if Path(root.target).expanduser().exists() else None
            key = (repo_key(repo), root.ref, local)
            graph = graphs.get(key)
            if graph is None:
                graph = graphs[key] = _target_graph(
                    env,
                    target=root.target,
                    ref=root.ref,
                    target_repo=repo,
                    direction="deps",
                    extra_warnings=[],
                ).graph
                for warning in graph.warnings:
                    ui.message("warning", f"{_root_label(root)}: {warning}")
                _report_depth_stops(graph, depth_limit, root=_root_label(root))
            matches.extend(find_matches(graph, wanted, source=source))
        emit(
            _table_paths(matches, fmt),
            fmt=fmt,
            columns=columns,
            kind="ansible.dependency_match",
            empty=f"No matching roots found{_within(depth_limit)}.",
        )


def graph_command(
    target: Annotated[str, Parameter(help=_TARGET_HELP)],
    /,
    *,
    ref: RefOption = None,
    direction: Annotated[
        Literal["up", "down", "both"],
        Parameter(
            name="--direction",
            help="up: what depends on TARGET (requires a source); down: what TARGET "
            "depends on; both (default).",
        ),
    ] = "both",
    all_refs: AllRefsOption = False,
    cached: Annotated[
        bool,
        Parameter(
            name="--cached",
            negative="",
            group=_SOURCE_DATA_GROUP,
            help=(
                "Read cached source data only, without checking remote refs. This is the "
                "default; the flag only makes it explicit."
            ),
        ),
    ] = False,
    depth: DepthOption = None,
    target_repo: TargetRepoOption = None,
    live: LiveOption = False,
    fmt: Annotated[
        GraphFormat,
        Parameter(name=["--format", "-f"], help="Output format: tree, mermaid or json."),
    ] = "tree",
    output: Annotated[
        Path | None,
        Parameter(name=["--out", "-o"], help="Write graph data to this file instead of stdout."),
    ] = None,
    options: GraphSourceOptions = _SOURCE_DEFAULTS,
) -> None:
    """Render the Ansible dependency graph of a role, repo, or playbook.

    For task-shaped answers as rows, use `deps`, `impact` or `find`.
    Inline source selectors (--org, --team, --repo, --path, --ref-kind,
    --ref-pattern, --ref-scan-default) are cached under a deterministic
    fingerprint key, so repeated identical invocations reuse the same scan.
    --parallel defaults to ansible.git_fetch_concurrency and is capped at 32.

    For example:

        untaped ansible graph acme/base --org acme --team platform --direction up --refresh
        untaped ansible graph acme/app --source prod --depth 2
        untaped ansible graph ./roles/web --target-repo acme/web --direction down
    """
    _check_all_refs(ref, all_refs=all_refs)
    depth_limit = _parse_depth(depth or "unlimited")
    with report_errors(), ExitStack() as stack:
        env = _graph_env(
            stack, options, command="graph", depth=depth_limit, live=live, all_refs=all_refs
        )
        built = _target_graph(
            env,
            target=target,
            ref=ref,
            target_repo=target_repo,
            direction=_DIRECTIONS[direction],
            extra_warnings=_refresh_selected(env, options),
        )
        ui = _report_warnings(built.graph.warnings)
        _report_depth_stops(built.graph, depth_limit)
        depth_note = "unlimited depth" if depth_limit is None else f"depth {depth_limit}"
        header_note = f"{built.data_source} · {depth_note}"
        _emit_graph(built.graph, fmt=fmt, output=output, ui=ui, header_note=header_note)


def _emit_reach(
    target: str,
    *,
    command: Literal["deps", "impact"],
    ref: str | None,
    all_refs: bool,
    target_repo: str | None,
    depth: str | None,
    live: bool,
    fmt: FormatOption,
    columns: list[str] | None,
    options: GraphSourceOptions,
) -> None:
    """Print one row per repository reached from ``target`` in one direction."""
    relation, kind, noun = _REACH_OUTPUT[command]
    _check_all_refs(ref, all_refs=all_refs)
    depth_limit = _parse_depth(depth or "unlimited")
    with report_errors(), ExitStack() as stack:
        env = _graph_env(
            stack, options, command=command, depth=depth_limit, live=live, all_refs=all_refs
        )
        graph = _target_graph(
            env,
            target=target,
            ref=ref,
            target_repo=target_repo,
            direction=command,
            extra_warnings=_refresh_selected(env, options),
        ).graph
        _report_warnings(graph.warnings)
        _report_depth_stops(graph, depth_limit)
        emit(
            _table_paths([hit.node for hit in reach(graph, relation)], fmt),
            fmt=fmt,
            columns=columns,
            kind=kind,
            empty=f"No {noun} found{_within(depth_limit)}.",
        )


def _table_paths[R: (ReachedNode, DependencyMatch)](rows: list[R], fmt: FormatOption) -> list[R]:
    """``rows`` with each path cut to ``first → … → last`` in a table; other formats keep it.

    The short path stays a one-item list, so each row keeps its record type.
    """
    if fmt != "table":
        return rows
    return [row.model_copy(update={"path": [_short_path(row.path)]}) for row in rows]


def _short_path(path: list[str]) -> str:
    return " → ".join(path if len(path) <= 2 else [path[0], "…", path[-1]])


def _check_all_refs(ref: str | None, *, all_refs: bool) -> None:
    if ref is not None and all_refs:
        raise_usage("--all-refs reads every ref; drop it or --ref")


def _report_warnings(warnings: Iterable[str]) -> UiContext:
    """Print warnings on stderr; return the UI context used."""
    ui = app_context().ui(strict=False)
    for warning in warnings:
        ui.message("warning", warning)
    return ui


_DIRECTIONS: dict[str, GraphDirection] = {"up": "impact", "down": "deps", "both": "both"}

_REACH_OUTPUT: dict[str, tuple[EdgeRelation, str, str]] = {
    "deps": ("requires", "ansible.dependency", "dependencies"),
    "impact": ("impacts", "ansible.dependent", "dependents"),
}
"""Per task command: the edges it walks, its record kind and its row noun."""


def _report_depth_stops(graph: DependencyGraph, depth: int | None, *, root: str = "") -> None:
    """Hint on stderr how many nodes the depth limit left unread, and how to read them."""
    count = sum(1 for node in graph.nodes if node.stopped == "depth")
    if depth is None or not count:
        return
    prefix = f"{root}: " if root else ""
    them = "it" if count == 1 else "them"
    echo(
        f"hint: {prefix}{plural(count, 'repo')} not read beyond --depth {depth}; "
        f"pass --depth unlimited to read {them}",
        err=True,
    )


def _within(depth: int | None) -> str:
    return "" if depth is None else f" within --depth {depth}"


def _graph_env(
    stack: ExitStack,
    options: GraphSourceOptions,
    *,
    command: GraphCommand,
    depth: int | None,
    live: bool,
    all_refs: bool,
) -> _GraphEnv:
    """Resolve settings and the selected source into what every root's build shares.

    Nothing is refreshed yet (see :func:`_refresh_selected`), so arguments can
    be checked against it first.
    """
    ctx = app_context()
    settings = get_config_section("ansible", AnsibleSettings)
    if options.backend is not None and not options.refresh:
        raise UsageError("--backend requires --refresh")
    if options.refresh and not (
        options.source or options.orgs or options.teams or options.repos or settings.default_source
    ):
        raise UsageError(
            "--refresh requires --source or inline source selectors (or ansible.default_source)"
        )
    aliases = AliasRepository().entries()
    github_settings = load_github_settings()
    github_host = github_web_host(github_settings.base_url)
    graph_source = _graph_source(options, default_source=settings.default_source)
    # Live reads resolve only the default branch; every ref exists only in a source's cache.
    if all_refs and live:
        raise UsageError("--all-refs reads cached source data; drop --live")
    if all_refs and not graph_source.selections:
        raise UsageError(
            "--all-refs reads cached source data; select one with --source NAME "
            "(or set ansible.default_source)"
        )
    sqlite_index = SqliteDependencyIndex(settings.index_path)
    index = _dependency_index_for_graph_source(sqlite_index, graph_source)
    return _GraphEnv(
        command=command,
        settings=settings,
        aliases=aliases,
        github_settings=github_settings,
        github_host=github_host,
        index=index,
        sqlite_index=sqlite_index,
        graph_source=graph_source,
        live=live,
        depth=depth,
        all_refs=all_refs,
        live_reads=_LiveReads(
            stack,
            github_settings=github_settings,
            http=ctx.http,
            wrapped=index,
            aliases=aliases,
            settings=settings,
            github_host=github_host,
        ),
    )


def _refresh_selected(env: _GraphEnv, options: GraphSourceOptions) -> list[str]:
    """Refresh the selected sources when ``--refresh`` asks; return partial-refresh warnings."""
    if not options.refresh:
        return []
    ctx = app_context()
    return _refresh_sources(
        env.graph_source,
        index=env.sqlite_index,
        aliases=env.aliases,
        settings=env.settings,
        github_settings=env.github_settings,
        http=ctx.http,
        concurrency=clamp_parallel(
            options.parallel or env.settings.git_fetch_concurrency,
            cap=GIT_PARALLEL_CAP,
            policy="Git fetch limit",
        ),
        backend=options.backend,
        ui=ctx.ui(strict=False),
    )


def _require_repo(env: _GraphEnv, target: str, *, target_repo: str | None = None) -> str:
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


def _refresh_sources(
    graph_source: _GraphSource,
    *,
    index: SqliteDependencyIndex,
    aliases: dict[str, str],
    settings: AnsibleSettings,
    github_settings: GithubSettings,
    http: HttpSettings,
    concurrency: int,
    backend: Literal["auto", "graphql", "git"] | None,
    ui: UiContext,
) -> list[str]:
    """Refresh every selected source; return warnings for partial refreshes."""
    warnings: list[str] = []
    for selection in graph_source.selections:
        result = run_source_refresh(
            selection.definition,
            source_key=selection.key,
            action="refreshed",
            label=selection.label,
            index=index,
            aliases=aliases,
            settings=settings,
            github_settings=github_settings,
            http=http,
            concurrency=concurrency,
            backend=backend,
            ui=ui,
        )
        if not result.completed:
            # Paused at the GitHub rate-limit floor: resuming later succeeds.
            raise AnsibleError(
                _refresh_pause_message(result, selection), category="unavailable", system="github"
            )
        if result.failures:
            warnings.append(
                f"refresh of {selection.label} had "
                f"{plural(len(result.failures), 'failure')}; "
                "data for those repos may be stale"
            )
    return warnings


@dataclass(frozen=True)
class _GraphEnv:
    """Everything a single root's graph build shares with the others."""

    command: GraphCommand
    settings: AnsibleSettings
    aliases: dict[str, str]
    github_settings: GithubSettings
    github_host: str | None
    index: DependencyIndex
    sqlite_index: SqliteDependencyIndex
    graph_source: _GraphSource
    live: bool
    depth: int | None
    all_refs: bool
    live_reads: _LiveReads


class _LiveReads:
    """One live GitHub read index shared by every root of a graph command.

    It opens the GitHub client on first use, so each repo/ref is read live
    once per command however many ``find`` roots reach it.
    """

    def __init__(
        self,
        stack: ExitStack,
        *,
        github_settings: GithubSettings,
        http: HttpSettings,
        wrapped: DependencyIndex,
        aliases: dict[str, str],
        settings: AnsibleSettings,
        github_host: str | None,
    ) -> None:
        self._stack = stack
        self._github_settings = github_settings
        self._http = http
        self._wrapped = wrapped
        self._aliases = aliases
        self._settings = settings
        self._github_host = github_host
        self._index: GithubDependencyIndex | None = None

    def index(self) -> GithubDependencyIndex:
        if self._index is None:
            github = self._stack.enter_context(GithubClient(self._github_settings, http=self._http))
            self._index = GithubDependencyIndex(
                github=github,
                wrapped=self._wrapped,
                aliases=self._aliases,
                dependency_paths=self._settings.dependency_paths,
                github_host=self._github_host,
                concurrency=self._settings.probe_concurrency,
            )
        return self._index


def _target_graph(
    env: _GraphEnv,
    *,
    target: str,
    ref: str | None,
    target_repo: str | None,
    direction: GraphDirection,
    extra_warnings: list[str],
) -> _BuiltGraph:
    """Build one root's graph, with its warnings attached, and say what it read."""
    target_repo_name = _require_repo(env, target, target_repo=target_repo)
    graph_source = env.graph_source
    direction, graph_warnings = _effective_direction(
        command=env.command,
        target=target,
        source_state=graph_source,
        index=env.sqlite_index,
        direction=direction,
        live=env.live,
    )
    refresh_hint = _refresh_hint(graph_source)
    parse_warnings: list[str] = []

    target_path = Path(target).expanduser()
    local_dependencies: _LocalDependencies | None = None
    if target_path.exists():
        local_dependencies = _local_dependencies(
            target_path,
            repo=target_repo_name,
            ref=ref,
            aliases=env.aliases,
            dependency_paths=env.settings.dependency_paths,
            github_host=env.github_host,
        )
        parse_warnings.extend(local_dependencies.warnings)

    graph, read_warnings, reads = _graph_for_target(
        env.index,
        request=GraphRequest(
            repo=target_repo_name,
            ref=ref,
            source_key=graph_source.key,
            direction=direction,
            depth=env.depth,
            # A local checkout is one state of its repo, overlaid at the ref-less node.
            all_refs=env.all_refs or local_dependencies is not None,
            stale_after=env.settings.stale_after,
            refresh_hint=refresh_hint,
        ),
        local=local_dependencies,
        use_live=_should_use_live_dependencies(
            direction=direction,
            source_key=graph_source.key,
            live=env.live,
        ),
        github_settings=env.github_settings,
        live_reads=env.live_reads,
    )
    parse_warnings.extend(read_warnings)

    graph = _with_graph_warnings(
        graph,
        [
            *extra_warnings,
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
    parts = ["local checkout"] if local_dependencies is not None else []
    if reads.source and graph_source.header:
        parts.append(graph_source.header)
    if reads.live:
        parts.append("downstream live" if reads.source else "live reads")
    return _BuiltGraph(graph, ", ".join(parts))


class _BuiltGraph(NamedTuple):
    graph: DependencyGraph
    data_source: str
    """What the build read, for the tree header: ``source prod, downstream live``."""


class _Reads(NamedTuple):
    """What a graph build read besides a local checkout."""

    source: bool
    """The cached source data."""
    live: bool
    """Downstream dependencies from GitHub."""


def _read_roots() -> list[tuple[GraphRoot, RootInput]]:
    """Roots from stdin (bare ``owner/repo@ref`` lines or pipe records), with their input.

    Identical inputs collapse; records naming one root stay one entry each.
    """
    piped = read_stdin_input(what="roots")
    if piped.records is None:
        return list(dict.fromkeys((root_from_line(value), RootInput()) for value in piped.values))
    roots: list[tuple[GraphRoot, RootInput]] = []
    for envelope in piped.records:
        root = root_from_record(envelope.record)
        if root is None:
            raise_usage(
                f"line {envelope.lineno}: record has no repository field ({', '.join(REPO_FIELDS)})"
            )
        roots.append((root, input_from_record(envelope.kind, envelope.record)))
    return list(dict.fromkeys(roots))


def _root_label(root: GraphRoot) -> str:
    return f"{root.target}@{root.ref}" if root.ref else root.target


@dataclass(frozen=True)
class _GraphSourceSelection:
    definition: SourceDefinition
    key: str
    label: str


@dataclass(frozen=True)
class _GraphSource:
    selections: tuple[_GraphSourceSelection, ...]
    key: str | None
    label: str | None
    saved: bool

    @property
    def header(self) -> str | None:
        """How the tree header names it: ``source prod``, ``sources a, b``, ``inline source K``."""
        if self.saved and len(self.selections) == 1:
            return f"source {self.label}"
        return self.label


@dataclass(frozen=True)
class _LocalDependencies:
    edges: list[IndexedDependency]
    warnings: list[str]


def _graph_source(options: GraphSourceOptions, *, default_source: str | None) -> _GraphSource:
    """The explicit selection, else ``ansible.default_source``, else no source."""
    has_inline = options.has_inline
    selected_source_names = _dedupe_preserve_order(options.source or [])
    if selected_source_names and has_inline:
        raise_usage(
            "--source cannot be combined with --org, --team, --repo, --path, "
            "--ref-kind, --ref-pattern, or --ref-scan-default"
        )
    from_default = False
    if not selected_source_names and not has_inline and default_source is not None:
        selected_source_names, from_default = [default_source], True
    if selected_source_names:
        source_repository = SourceRepository()
        selections: list[_GraphSourceSelection] = []
        for source_name in selected_source_names:
            source = source_repository.get(source_name)
            if source is None:
                known = sorted(entry.name for entry in source_repository.entries())
                message = not_found("source", source_name, known=known)
                if from_default:
                    # The user's own setting names a missing source: fix the config.
                    message = f"{message} (set by ansible.default_source)"
                raise AnsibleError(
                    message,
                    category="config" if from_default else "not_found",
                    system="local",
                )
            selections.append(
                _GraphSourceSelection(
                    definition=source,
                    key=source_commands._saved_source_key(source_name),
                    label=source_name,
                )
            )
        return _GraphSource(
            selections=tuple(selections),
            key=_graph_source_key(selections),
            label=_graph_source_label(selections),
            saved=True,
        )
    if has_inline:
        source = source_commands._source_definition(
            name="<inline>",
            orgs=options.orgs,
            teams=options.teams,
            repos=options.repos,
            paths=options.paths,
            ref_kinds=options.ref_kinds,
            ref_patterns=options.ref_patterns,
            ref_scan_default=options.ref_scan_default,
        )
        key = source_commands._inline_source_key(source)
        return _GraphSource(
            selections=(
                _GraphSourceSelection(
                    definition=source,
                    key=key,
                    label=f"inline source {key.removeprefix('inline:')}",
                ),
            ),
            key=key,
            label=f"inline source {key.removeprefix('inline:')}",
            saved=False,
        )
    return _GraphSource(selections=(), key=None, label=None, saved=False)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _graph_source_key(selections: list[_GraphSourceSelection]) -> str:
    if len(selections) == 1:
        return selections[0].key
    names = ",".join(selection.key.removeprefix("source:") for selection in selections)
    return f"sources:{names}"


def _graph_source_label(selections: list[_GraphSourceSelection]) -> str:
    if len(selections) == 1:
        return selections[0].label
    return f"sources {', '.join(selection.label for selection in selections)}"


def _dependency_index_for_graph_source(
    index: DependencyIndex,
    source_state: _GraphSource,
) -> DependencyIndex:
    if len(source_state.selections) <= 1:
        return index
    return MultiSourceDependencyIndex(
        index,
        tuple(selection.key for selection in source_state.selections),
    )


def _effective_direction(
    *,
    command: GraphCommand,
    target: str,
    source_state: _GraphSource,
    index: SqliteDependencyIndex,
    direction: GraphDirection,
    live: bool,
) -> tuple[GraphDirection, list[str]]:
    if not source_state.selections:
        if direction == "deps":
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
    missing = tuple(
        selection for selection in source_state.selections if index.status(selection.key) is None
    )
    if missing and live and direction != "impact":
        # --live reads downstream from GitHub, so it never needs the cache;
        # only the upstream half of --direction both does.
        if direction == "deps":
            return direction, []
        labels = ", ".join(selection.label for selection in missing)
        return "deps", [
            f"only showing downstream; upstream omitted because {labels} has no cached "
            f"source data. Run {_source_refresh_commands(missing)} first."
            if source_state.saved
            else "only showing downstream; upstream omitted because the inline source has "
            "no cached source data. Re-run with `--refresh` first."
        ]
    if missing:
        raise AnsibleError(
            _missing_source_index_message(
                command, target, source_state, missing, direction=direction
            )
        )
    return direction, []


def _missing_source_index_message(
    command: GraphCommand,
    target: str,
    source_state: _GraphSource,
    missing: tuple[_GraphSourceSelection, ...],
    *,
    direction: GraphDirection,
) -> str:
    live_hint = (
        " Or pass `--live` to read downstream dependencies from GitHub without cached data."
        if direction == "deps"
        else ""
    )
    if source_state.saved:
        refresh_commands = _source_refresh_commands(missing)
        source_flags = " ".join(
            f"--source {selection.label}" for selection in source_state.selections
        )
        rerun = _rerun_with_refresh(command, target, source_flags, direction=direction)
        if len(missing) > 1:
            labels = ", ".join(repr(selection.label) for selection in missing)
            return (
                f"no cached source data found for sources {labels}. Run: {refresh_commands}. "
                f"Or {rerun}.{live_hint}"
            )
        label = missing[0].label
        return (
            f"no cached source data found for source {label!r}. Run: {refresh_commands}. "
            f"Or {rerun}.{live_hint}"
        )
    return (
        "no cached source data found for inline source. Re-run this command with "
        f"`--refresh` to scan GitHub and cache the result.{live_hint}"
    )


def _rerun_with_refresh(
    command: GraphCommand, target: str, source_flags: str, *, direction: GraphDirection
) -> str:
    """The exact re-run-with-``--refresh`` instruction for ``command``."""
    if command == "find":
        return "re-run this command with `--refresh`"
    direction_flag = _DIRECTION_FLAGS[direction] if command == "graph" else ""
    return (
        f"re-run with: `untaped ansible {command} {target} {source_flags}{direction_flag} "
        "--refresh`"
    )


_DIRECTION_FLAGS: dict[GraphDirection, str] = {
    "impact": " --direction up",
    "deps": " --direction down",
    "both": "",
}


def _resolve_target_repo(
    target: str,
    aliases: dict[str, str],
    *,
    github_host: str | None,
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
    github_settings: GithubSettings,
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
                read_index,
                local.edges,
                authoritative_sources={(request.repo, request.ref)},
            )
        return BuildGraph(read_index)(request.model_copy(update={"live": use_live}))

    if not use_live:
        return build(index), [], _Reads(source=request.source_key is not None, live=False)
    if local is not None and request.source_key is None and github_settings.token is None:
        warnings = []
        if any(edge.dependency_repo is not None for edge in local.edges):
            warnings.append(
                "transitive dependencies were not expanded: pass --source NAME to use "
                "cached source data, or configure github.token for live GitHub reads"
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


def _refresh_hint(source_state: _GraphSource) -> str | None:
    """Compose the exact fix command surfaced in stale/missing-ref warnings."""
    if not source_state.selections:
        return None
    if source_state.saved:
        commands = _source_refresh_commands(source_state.selections)
        return f"Run {commands} to update it."
    return "Re-run this command with `--refresh` to update it."


def _source_refresh_commands(selections: tuple[_GraphSourceSelection, ...]) -> str:
    """Join the exact `untaped ansible source refresh NAME` commands for selections."""
    return " and ".join(
        f"`untaped ansible source refresh {selection.label}`" for selection in selections
    )


def _refresh_pause_message(result: RefreshResult, selection: _GraphSourceSelection) -> str:
    reason = result.pause_reason or "source refresh paused before completion"
    if selection.key.startswith("source:"):
        return f"{reason}; resume with `untaped ansible source refresh {selection.label}`"
    return f"{reason}; re-run this command with `--refresh` to resume"


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
    if direction == "impact":
        label = source_label or "source"
        return [f"no cached upstream dependents found for {graph.target_id} in {label}"]
    label = source_label or "source"
    return [
        f"no declared downstream dependencies or cached upstream dependents found for "
        f"{graph.target_id} in {label}; checked configured dependency paths: {paths}"
    ]


def _should_use_live_dependencies(
    *,
    direction: GraphDirection,
    source_key: str | None,
    live: bool,
) -> bool:
    if direction == "impact":
        return False
    if source_key is None:
        return True
    return live


def _emit_graph(
    graph: DependencyGraph,
    *,
    fmt: GraphFormat,
    output: Path | None,
    ui: UiContext,
    header_note: str,
) -> None:
    """Print the rendered graph (a styled tree on a terminal), or write it plain to ``output``."""
    if fmt == "tree":
        lines = tree_lines(graph, glyphs=tree_glyphs(ui), header_note=header_note)
        if output is None:
            print_tree(lines, ui)
            return
        rendered = plain_text(lines)
    else:
        rendered = render_graph(graph, fmt)
        if output is None:
            echo(rendered)
            return
    output.expanduser().parent.mkdir(parents=True, exist_ok=True)
    output.expanduser().write_text(rendered)


def _parse_depth(value: str) -> int | None:
    if value == "unlimited":
        return None
    try:
        depth = int(value)
    except ValueError:
        raise_usage("--depth must be an integer or 'unlimited'")
    if depth < 0:
        raise_usage("--depth must be >= 0")
    return depth
