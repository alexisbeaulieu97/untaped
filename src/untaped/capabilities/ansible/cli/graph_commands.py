"""Graph command and graph-specific CLI helpers for the Ansible tool."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from cyclopts import App, Group, Parameter, validators

import untaped.capabilities.ansible.cli.source_commands as source_commands
from untaped.capabilities.ansible.application import BuildGraph, GraphRequest
from untaped.capabilities.ansible.application.ports import DependencyIndex
from untaped.capabilities.ansible.application.refresh_index import RefreshResult
from untaped.capabilities.ansible.cli.refresh import (
    GIT_PARALLEL_CAP,
    format_skipped_dependency_file,
    ignored_collections_warning,
    run_source_refresh,
    warn_deprecated_settings,
)
from untaped.capabilities.ansible.domain.graph import DependencyGraph
from untaped.capabilities.ansible.domain.identity import IdentityResolver, github_web_host
from untaped.capabilities.ansible.domain.models import DependencyDeclaration, ParseWarning
from untaped.capabilities.ansible.domain.parser import parse_dependency_file
from untaped.capabilities.ansible.domain.payloads import IndexedDependency, SkippedDependencyFile
from untaped.capabilities.ansible.domain.renderers import GraphFormat, render_graph
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
from untaped.capabilities.github.ansible import GithubClient, GithubSettings
from untaped.capabilities.github.ansible import github_settings as load_github_settings
from untaped.capability_api import (
    HttpSettings,
    ParallelOption,
    UntapedError,
    app_context,
    clamp_parallel,
    deprecated_alias,
    echo,
    get_config_section,
    not_found,
    plural,
    raise_usage,
    report_errors,
)

GraphDirection = Literal["deps", "impact", "both"]
GraphFormatOption = Annotated[
    GraphFormat,
    Parameter(name=["--format", "-f"], help="Graph output format."),
]
BackendOption = Annotated[
    Literal["auto", "graphql", "git"] | None,
    Parameter(
        name="--backend",
        help="Ref probe backend for source refresh: auto, graphql, or git.",
    ),
]

# LimitedChoice() defaults to at-most-one selection — cyclopts' MutuallyExclusive
# is an untyped alias for exactly this, so the typed parent is used directly.
_DIRECTION_GROUP = Group("Direction", validator=validators.LimitedChoice())
_SOURCE_DATA_GROUP = Group("Source Data", validator=validators.LimitedChoice())


def register_graph_command(app: App) -> None:
    """Register graph commands on the Ansible root app."""
    app.command(graph_command, name="graph")
    deprecated_alias(app["graph"], "--concurrency", "--parallel")
    deprecated_alias(app["graph"], "--output", "--out")


def graph_command(
    target: Annotated[
        str,
        Parameter(help="Target repo, GitHub URL, alias, or local path."),
    ],
    /,
    *,
    ref: Annotated[
        str | None,
        Parameter(
            name="--ref",
            help="Target branch, tag, or SHA for live dependency reads and cached upstream lookup.",
        ),
    ] = None,
    source: Annotated[
        list[str] | None,
        Parameter(
            name="--source",
            help="Saved source to use for cached graph data and upstream impact; repeat to union.",
            consume_multiple=False,
            negative="",
        ),
    ] = None,
    upstream: Annotated[
        bool,
        Parameter(
            name="--upstream",
            negative="",
            group=_DIRECTION_GROUP,
            help="Show repos that depend on TARGET (reverse impact; requires a source).",
        ),
    ] = False,
    downstream: Annotated[
        bool,
        Parameter(
            name="--downstream",
            negative="",
            group=_DIRECTION_GROUP,
            help="Show what TARGET depends on (works without a source).",
        ),
    ] = False,
    both: Annotated[
        bool,
        Parameter(
            name="--both",
            negative="",
            group=_DIRECTION_GROUP,
            help="Show upstream and downstream (default). Upstream still requires a source.",
        ),
    ] = False,
    refresh: Annotated[
        bool,
        Parameter(
            name="--refresh",
            negative="",
            group=_SOURCE_DATA_GROUP,
            help="Refresh source data before graphing.",
        ),
    ] = False,
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
    parallel: ParallelOption | None = None,
    backend: BackendOption = None,
    live: Annotated[
        bool,
        Parameter(
            name="--live",
            negative="",
            group=_SOURCE_DATA_GROUP,
            help=(
                "Use live GitHub reads for downstream graphing even when source data is configured."
            ),
        ),
    ] = False,
    depth: Annotated[str, Parameter(name="--depth", help="Traversal depth or 'unlimited'.")] = "3",
    target_repo: Annotated[
        str | None,
        Parameter(name="--target-repo", help="Canonical owner/repo override for local targets."),
    ] = None,
    orgs: Annotated[
        list[str] | None,
        Parameter(
            name="--org", help="Inline source GitHub org.", consume_multiple=False, negative=""
        ),
    ] = None,
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
    ] = None,
    source_repos: Annotated[
        list[str] | None,
        Parameter(
            name="--repo",
            help="Inline source GitHub repo as owner/name.",
            consume_multiple=False,
            negative="",
        ),
    ] = None,
    paths: Annotated[
        list[str] | None,
        Parameter(
            name="--path",
            help="Inline source dependency path.",
            consume_multiple=False,
            negative="",
        ),
    ] = None,
    ref_kinds: Annotated[
        list[str] | None,
        Parameter(
            name="--ref-kind",
            help="Inline source ref namespace to scan: heads or tags; omit for configured default.",
            consume_multiple=False,
            negative="",
        ),
    ] = None,
    ref_patterns: Annotated[
        list[str] | None,
        Parameter(
            name="--ref-pattern",
            help="Inline source fnmatch pattern for branch/tag names; omit for configured default.",
            consume_multiple=False,
            negative="",
        ),
    ] = None,
    ref_scan_default: Annotated[
        Literal["all", "default_branch"] | None,
        Parameter(
            name="--ref-scan-default",
            help="Inline source scan strategy: all refs or only each repo's default branch.",
        ),
    ] = None,
    fmt: GraphFormatOption = "tree",
    output: Annotated[
        Path | None,
        Parameter(name=["--out", "-o"], help="Write graph data to this file instead of stdout."),
    ] = None,
) -> None:
    """Graph Ansible dependency relationships for a role, repo, or playbook.

    Inline source selectors (--org, --team, --repo, --path, --ref-kind,
    --ref-pattern, --ref-scan-default) are cached under a deterministic
    fingerprint key, so repeated identical invocations reuse the same scan.
    --parallel defaults to ansible.git_fetch_concurrency and is capped at 32.

    For example:

        untaped ansible graph acme/base --org acme --team platform --upstream --refresh
        untaped ansible graph acme/app --source prod --both --cached
        untaped ansible graph ./roles/web --target-repo acme/web --downstream
    """
    depth_limit = _parse_depth(depth)
    if refresh and not any((source, orgs, teams, source_repos)):
        raise_usage("--refresh requires --source or inline source selectors")
    if backend is not None and not refresh:
        raise_usage("--backend requires --refresh")
    with report_errors():
        ctx = app_context()
        settings = get_config_section("ansible", AnsibleSettings)
        warn_deprecated_settings(settings, ui=ctx.ui(strict=False))
        aliases = AliasRepository().entries()
        github_settings = load_github_settings()
        github_host = github_web_host(github_settings.base_url)
        target_repo_name = target_repo or _resolve_target_repo(
            target, aliases, github_host=github_host
        )
        if target_repo_name is None:
            message = f"could not resolve target to a GitHub repo: {target!r}"
            if Path(target).expanduser().exists():
                message = (
                    f"{message}; the local path is not the top level of a Git checkout "
                    "with a remote pointing at GitHub. Pass --target-repo OWNER/NAME"
                )
            raise UntapedError(message)

        direction = _graph_direction(upstream=upstream, downstream=downstream, both=both)
        git_concurrency = clamp_parallel(
            parallel or settings.git_fetch_concurrency,
            cap=GIT_PARALLEL_CAP,
            policy="Git fetch limit",
        )
        graph_source = _graph_source(
            source_names=source,
            orgs=orgs,
            teams=teams,
            repos=source_repos,
            paths=paths,
            ref_kinds=ref_kinds,
            ref_patterns=ref_patterns,
            ref_scan_default=ref_scan_default,
        )
        sqlite_index = SqliteDependencyIndex(settings.index_path)
        index: DependencyIndex = _dependency_index_for_graph_source(sqlite_index, graph_source)
        should_refresh_source = refresh
        refresh_warnings: list[str] = []
        if should_refresh_source:
            for selection in graph_source.selections:
                result = run_source_refresh(
                    selection.definition,
                    source_key=selection.key,
                    action="refreshed" if refresh else "checked",
                    label=selection.label,
                    index=sqlite_index,
                    aliases=aliases,
                    settings=settings,
                    github_settings=github_settings,
                    http=ctx.http,
                    concurrency=git_concurrency,
                    backend=backend,
                    ui=ctx.ui(strict=False),
                )
                if not result.completed:
                    raise UntapedError(_refresh_pause_message(result, selection))
                if result.failures:
                    refresh_warnings.append(
                        f"refresh of {selection.label} had "
                        f"{plural(len(result.failures), 'failure')}; "
                        "data for those repos may be stale"
                    )

        direction, graph_warnings = _effective_direction(
            target=target,
            source_state=graph_source,
            index=sqlite_index,
            direction=direction,
            live=live,
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
                aliases=aliases,
                dependency_paths=settings.dependency_paths,
                github_host=github_host,
            )
            parse_warnings.extend(local_dependencies.warnings)

        graph, read_warnings = _graph_for_target(
            index,
            request=GraphRequest(
                repo=target_repo_name,
                ref=ref,
                source_key=graph_source.key,
                direction=direction,
                depth=depth_limit,
                stale_after=settings.stale_after,
                refresh_hint=refresh_hint,
            ),
            local=local_dependencies,
            use_live=_should_use_live_dependencies(
                direction=direction,
                source_key=graph_source.key,
                live=live,
            ),
            github_settings=github_settings,
            http=ctx.http,
            aliases=aliases,
            settings=settings,
            github_host=github_host,
        )
        parse_warnings.extend(read_warnings)

        graph = _with_graph_warnings(
            graph,
            [
                *refresh_warnings,
                *graph_warnings,
                *parse_warnings,
                *_empty_graph_warnings(
                    graph,
                    direction=direction,
                    dependency_paths=settings.dependency_paths,
                    source_label=graph_source.label,
                ),
            ],
        )
        _emit_graph(graph, fmt=fmt, output=output)


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


@dataclass(frozen=True)
class _LocalDependencies:
    edges: list[IndexedDependency]
    warnings: list[str]


def _graph_direction(*, upstream: bool, downstream: bool, both: bool) -> GraphDirection:
    # Mutual exclusion is enforced at parse time by _DIRECTION_GROUP.
    del both
    if upstream:
        return "impact"
    if downstream:
        return "deps"
    return "both"


def _graph_source(
    *,
    source_names: list[str] | None,
    orgs: list[str] | None,
    teams: list[str] | None,
    repos: list[str] | None,
    paths: list[str] | None,
    ref_kinds: list[str] | None,
    ref_patterns: list[str] | None,
    ref_scan_default: Literal["all", "default_branch"] | None,
) -> _GraphSource:
    has_inline = any((orgs, teams, repos, paths, ref_kinds, ref_patterns, ref_scan_default))
    selected_source_names = _dedupe_preserve_order(source_names or [])
    if selected_source_names and has_inline:
        raise_usage(
            "--source cannot be combined with --org, --team, --repo, --path, "
            "--ref-kind, --ref-pattern, or --ref-scan-default"
        )
    if selected_source_names:
        source_repository = SourceRepository()
        selections: list[_GraphSourceSelection] = []
        for source_name in selected_source_names:
            source = source_repository.get(source_name)
            if source is None:
                known = sorted(entry.name for entry in source_repository.entries())
                raise UntapedError(not_found("source", source_name, known=known))
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
            orgs=orgs,
            teams=teams,
            repos=repos,
            paths=paths,
            ref_kinds=ref_kinds,
            ref_patterns=ref_patterns,
            ref_scan_default=ref_scan_default,
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
            "upstream requires --source NAME or inline selectors like --org, --team, or --repo"
        )
        if direction == "impact":
            raise UntapedError(message)
        return "deps", [
            "only showing downstream; upstream omitted because no source is configured. "
            "Pass --source NAME or inline selectors."
        ]
    missing = tuple(
        selection for selection in source_state.selections if index.status(selection.key) is None
    )
    if missing and live and direction != "impact":
        # --live reads downstream from GitHub, so it never needs the cache;
        # only the upstream half of --both does.
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
        raise UntapedError(
            _missing_source_index_message(target, source_state, missing, direction=direction)
        )
    return direction, []


def _missing_source_index_message(
    target: str,
    source_state: _GraphSource,
    missing: tuple[_GraphSourceSelection, ...],
    *,
    direction: GraphDirection,
) -> str:
    direction_flag = _DIRECTION_FLAGS[direction]
    live_hint = (
        " Or pass `--live` to read downstream dependencies from GitHub without cached data."
        if direction == "deps"
        else ""
    )
    if source_state.saved:
        refresh_commands = _source_refresh_commands(missing)
        if len(missing) > 1:
            labels = ", ".join(repr(selection.label) for selection in missing)
            source_flags = " ".join(
                f"--source {selection.label}" for selection in source_state.selections
            )
            return (
                f"no cached source data found for sources {labels}. Run: {refresh_commands}. "
                f"Or re-run graph with: "
                f"`untaped ansible graph {target} {source_flags}{direction_flag} --refresh`."
                f"{live_hint}"
            )
        label = missing[0].label
        return (
            f"no cached source data found for source {label!r}. Run: {refresh_commands}. "
            f"Or re-run graph with: "
            f"`untaped ansible graph {target} --source {label}{direction_flag} --refresh`."
            f"{live_hint}"
        )
    return (
        "no cached source data found for inline source. Re-run this graph command with "
        f"`--refresh` to scan GitHub and cache the result.{live_hint}"
    )


_DIRECTION_FLAGS: dict[GraphDirection, str] = {
    "impact": " --upstream",
    "deps": " --downstream",
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
    http: HttpSettings,
    aliases: dict[str, str],
    settings: AnsibleSettings,
    github_host: str | None,
) -> tuple[DependencyGraph, list[str]]:
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
        return BuildGraph(read_index)(request)

    if not use_live:
        return build(index), []
    if local is not None and request.source_key is None and github_settings.token is None:
        warnings = []
        if any(edge.dependency_repo is not None for edge in local.edges):
            warnings.append(
                "transitive dependencies were not expanded: pass --source NAME to use "
                "cached source data, or configure github.token for live GitHub reads"
            )
        return build(NullDependencyIndex()), warnings
    with GithubClient(github_settings, http=http) as github:
        live_index = GithubDependencyIndex(
            github=github,
            wrapped=index,
            aliases=aliases,
            dependency_paths=settings.dependency_paths,
            github_host=github_host,
            concurrency=settings.probe_concurrency,
        )
        graph = build(live_index)
    return graph, [*live_index.errors, *_live_parse_warning_messages(live_index.warnings)]


def _refresh_hint(source_state: _GraphSource) -> str | None:
    """Compose the exact fix command surfaced in stale/missing-ref warnings."""
    if not source_state.selections:
        return None
    if source_state.saved:
        commands = _source_refresh_commands(source_state.selections)
        return f"Run {commands} to update it."
    return "Re-run this graph command with `--refresh` (without `--cached`) to update it."


def _source_refresh_commands(selections: tuple[_GraphSourceSelection, ...]) -> str:
    """Join the exact `untaped ansible source refresh NAME` commands for selections."""
    return " and ".join(
        f"`untaped ansible source refresh {selection.label}`" for selection in selections
    )


def _refresh_pause_message(result: RefreshResult, selection: _GraphSourceSelection) -> str:
    reason = result.pause_reason or "source refresh paused before completion"
    if selection.key.startswith("source:"):
        return f"{reason}; resume with `untaped ansible source refresh {selection.label}`"
    return f"{reason}; re-run this graph command with `--refresh` to resume"


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


def _emit_graph(graph: DependencyGraph, *, fmt: GraphFormat, output: Path | None) -> None:
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
