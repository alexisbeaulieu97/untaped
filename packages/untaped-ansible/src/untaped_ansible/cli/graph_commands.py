"""Dependency-graph commands (deps, impact, find, graph) and their CLI helpers.

``deps``, ``impact`` and ``find`` answer one question each and print rows
(``ansible.dependency``, ``ansible.dependent``, ``ansible.dependency_match``);
``graph`` renders the whole graph as a tree, Mermaid or JSON document, both
directions at once by default. All four report warnings on stderr, share the
source-data flags of :class:`GraphSourceOptions` and fall back to
``ansible.default_source`` when no source is selected.
"""

from __future__ import annotations

import keyword
import os
import re
import shlex
from collections.abc import Callable, Iterable
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal, NamedTuple

from cyclopts import App, Group, Parameter, validators

import untaped_ansible.cli.source_commands as source_commands
from untaped.sdk import (
    ColumnsOption,
    Diagnostics,
    FormatOption,
    HttpSettings,
    NeverRefreshed,
    ParallelOption,
    UiContext,
    UsageError,
    app_context,
    clamp_parallel,
    echo,
    emit,
    finish,
    get_config_section,
    not_found,
    plural,
    print_tree,
    q,
    raise_usage,
    read_stdin_input,
    refresh_option,
    report_errors,
    run_hint,
    tree_text,
)
from untaped_ansible.application.graph import BuildGraph, GraphRequest
from untaped_ansible.application.ports import DependencyIndex
from untaped_ansible.cli.refresh import (
    GIT_PARALLEL_CAP,
    Backend,
    RefreshWiring,
    SelectedSource,
    format_skipped_dependency_file,
    ignored_collections_warning,
    serve_sources,
)
from untaped_ansible.domain.containment import find_matches
from untaped_ansible.domain.graph import DependencyGraph, EdgeRelation
from untaped_ansible.domain.graph_roots import (
    REPO_FIELDS,
    GraphRoot,
    RootInput,
    input_from_record,
    root_from_line,
    root_from_record,
)
from untaped_ansible.domain.identity import IdentityResolver, repo_key
from untaped_ansible.domain.models import DependencyDeclaration, ParseWarning
from untaped_ansible.domain.parser import parse_dependency_file
from untaped_ansible.domain.payloads import IndexedDependency, SkippedDependencyFile
from untaped_ansible.domain.reach import Reach, reach
from untaped_ansible.domain.renderers import GraphFormat, graph_tree, render_graph
from untaped_ansible.domain.variable_queries import (
    dependent_uses,
    render_key_path,
    skipped_files,
    unanalysed,
)
from untaped_ansible.errors import AnsibleError
from untaped_ansible.infrastructure import (
    AliasRepository,
    GithubDependencyIndex,
    MultiSourceDependencyIndex,
    NullDependencyIndex,
    OverlayDependencyIndex,
    SourceRepository,
    SqliteDependencyIndex,
    local_remote_url,
    open_index,
)
from untaped_ansible.records import (
    DependencyMatch,
    ReachedDependency,
    ReachedDependent,
    ReachedRow,
    VariableUseHit,
)
from untaped_ansible.settings import INDEX, AnsibleSettings, SourceDefinition
from untaped_github.api import (
    GithubClient,
    GithubSettings,
    github_token_found,
    github_web_host,
)
from untaped_github.api import github_settings as load_github_settings

_diag = Diagnostics()

GraphDirection = Literal["deps", "impact", "both"]
GraphCommand = Literal["graph", "deps", "impact", "find"]
BackendOption = Annotated[
    Backend | None,
    Parameter(
        name="--backend",
        help="Ref probe backend for a refresh this command runs: auto, graphql, or git; not "
        "with --cached.",
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
    Parameter(
        name="--target-repo",
        help="owner/repo of a local target; needed for a subdirectory of a checkout, whose "
        "remote is read only at the checkout's top level.",
    ),
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
# Keyed below 100 so it lists before core's panels, keeping Global options last.
_SOURCE_DATA_GROUP = Group("Source Data", sort_key=10, validator=validators.LimitedChoice())
LiveOption = Annotated[
    bool,
    Parameter(
        name="--live",
        negative="",
        group=_SOURCE_DATA_GROUP,
        help="Read downstream dependencies live from GitHub even when a source is selected; "
        "not with --refresh or --cached.",
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
    refresh: Annotated[bool | None, refresh_option(INDEX), Parameter(group=_SOURCE_DATA_GROUP)] = (
        None
    )
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

    Reads a source's data when one is selected (--source, inline selectors,
    or ansible.default_source), refreshed first when older than its max age,
    and GitHub live otherwise (or with --live); ``untaped agents ansible
    source-data`` says which data answers. Each row carries the ref as
    declared, the shortest path from ROLE and the ROLE ref it was reached
    from; each repo appears once per ROLE ref. ``untaped agents ansible
    resolution`` explains refs, local targets and where a walk stops.

    Examples
    --------
    ```console
    $ untaped ansible deps acme/web --ref v2.1.0
    $ untaped ansible deps ./roles/web --target-repo acme/web --format json
    ```
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
    uses: Annotated[
        list[str] | None,
        Parameter(
            name="--uses",
            consume_multiple=False,
            negative="",
            help=(
                "Keep the dependents that use this variable, one row per place: defines (a "
                "top-level key under defaults/ or vars/, or an argument_specs option), sets "
                "(any other YAML key of that name, register or loop_var; a module argument of "
                "that name counts too) or reads (a Jinja expression). Repeat for any of "
                "several. INI inventories and CI config are not read."
            ),
        ),
    ] = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    options: GraphSourceOptions = _SOURCE_DEFAULTS,
) -> None:
    """Show what depends on ROLE (upstream), one row per repository per root ref.

    Reads a source's data (--source, inline selectors, or
    ansible.default_source), refreshed first when older than its max age;
    ``untaped agents ansible source-data`` says which data answers. Each row
    carries the ref it declares, its shortest path to ROLE and the ROLE ref
    it was reached from (``root_ref`` tells a dependent's pinned refs apart).

    With --uses, each row is instead one place a dependent defines, sets or
    reads a variable, read at the commit the source indexed; a dependent
    with no row does not mention it. Dependents whose refresh failed have no
    variable facts: an error names them and how to refresh them
    (``source refresh NAME``, or --refresh for inline selectors), and the
    command exits 1. A hint that ROLE defines no such variable usually means
    a typo.

    Examples
    --------
    ```console
    $ untaped ansible impact acme/base --source platform
    $ untaped ansible impact acme/base --ref main --format pipe
    $ untaped ansible impact acme/base --uses base_port --format json
    ```
    """
    if uses:
        _emit_uses(
            role,
            ref=ref,
            target_repo=target_repo,
            depth=depth,
            names=_variable_names(uses),
            fmt=fmt,
            columns=columns,
            options=options,
        )
        return
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

    Rows from pipe records carry that record's kind, id and name, so they
    join back to their input. Every input record gets its own rows, even
    when records share a root; identical bare lines collapse. A root given
    without a ref, or with an empty one, is read at its default branch.

    A repo reached through two declared refs gives two rows; an unpinned and
    a default-branch-pinned declaration of one repo give one. A root that
    never reaches the repo gives none; with --depth, no row only means "not
    within that depth". Each root's warnings go to stderr, prefixed with the
    root.

    Examples
    --------
    ```console
    $ untaped ansible find acme/base --root acme/site@main --root acme/app
    $ untaped awx job-templates list --with-scm -f pipe | untaped ansible find acme/base --stdin
    ```
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
        resolved = [(root, source, _require_repo(env, root.target)) for root, source in inputs]

        def read() -> _Found:
            # Spellings of one repo (URL, .git, SSH, alias) share one graph build.
            graphs: dict[tuple[str, str | None, str | None], DependencyGraph] = {}
            found = _Found()
            for root, source, repo in resolved:
                local = root.target if Path(root.target).expanduser().exists() else None
                key = (repo_key(repo), root.ref, local)
                graph = graphs.get(key)
                if graph is None:
                    graph = graphs[key] = _target_graph(
                        env, target=root.target, ref=root.ref, target_repo=repo, direction="deps"
                    ).graph
                    found.graphs.append((_root_label(root), graph))
                found.matches.extend(find_matches(graph, wanted, source=source))
            return found

        found = _answer(env, options, "deps", read, empty=lambda found: not found.matches)
        for label, graph in found.graphs:
            for warning in graph.warnings:
                _diag.warning(f"{label}: {warning}")
            _report_depth_stops(graph, depth_limit, root=label)
        emit(
            _table_paths(found.matches, fmt),
            fmt=fmt,
            columns=columns,
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
    --parallel defaults to ansible.git_fetch_parallel and is capped at 32.

    The tree's first line names the target, the data it read and the depth;
    a "used by" section lists dependents and a "depends on" section
    dependencies. Each repo line names the file that declares the edge, plus
    ``unpinned`` (no version) or ``pins X`` (the declared version differs
    from the resolved ref). A shared subtree prints once, numbered ``[n]``;
    a later occurrence holds one ``… see [n]`` line instead of its branches,
    and a repo already on the path says ``↻ cycle``
    (``(cycle)`` in ASCII); a stopped repo is marked ``…`` with a
    ``not read:`` note. The summary counts repos, edges, cycles (a component
    with too many cycles to list counts as one cyclic group), unresolved
    dependencies and stopped repos. Lines too wide for the terminal end in
    ``…``. Warnings go to stderr, even with --out.

    The JSON document has ``target_id``, ``nodes`` (each with ``stopped`` as
    on rows), ``edges``, ``cycles`` and ``warnings``. An edge's ``id`` is the
    same for every declaration of one dependency, so duplicates collapse to
    one edge. A cycle has ``kind``, ``relation``, ``node_ids`` and
    ``edge_ids``: ``cycle`` is a closed ordered path, ``scc_group`` a sorted
    set of nodes and their internal edges, used when a component has too
    many cycles to list. Node ids are lowercase; labels keep the display
    casing.

    Examples
    --------
    ```console
    $ untaped ansible graph acme/base --org acme --team acme/platform --direction up --refresh
    $ untaped ansible graph acme/app --source prod --depth 2
    $ untaped ansible graph ./roles/web --target-repo acme/web --direction down
    ```
    """
    _check_all_refs(ref, all_refs=all_refs)
    depth_limit = _parse_depth(depth or "unlimited")
    with report_errors(), ExitStack() as stack:
        env = _graph_env(
            stack, options, command="graph", depth=depth_limit, live=live, all_refs=all_refs
        )
        repo = _require_repo(env, target, target_repo=target_repo)
        built = _answer(
            env,
            options,
            _DIRECTIONS[direction],
            lambda: _target_graph(
                env, target=target, ref=ref, target_repo=repo, direction=_DIRECTIONS[direction]
            ),
            empty=lambda built: not built.graph.edges,
        )
        _report_warnings(built.graph.warnings)
        _report_depth_stops(built.graph, depth_limit)
        depth_note = "unlimited depth" if depth_limit is None else f"depth {depth_limit}"
        header_note = f"{built.data_source} · {depth_note}"
        _emit_graph(
            built.graph,
            fmt=fmt,
            output=output,
            ui=app_context().ui(strict=False),
            header_note=header_note,
        )


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
    relation, model, noun = _REACH_OUTPUT[command]
    _check_all_refs(ref, all_refs=all_refs)
    depth_limit = _parse_depth(depth or "unlimited")
    with report_errors(), ExitStack() as stack:
        env = _graph_env(
            stack, options, command=command, depth=depth_limit, live=live, all_refs=all_refs
        )
        repo = _require_repo(env, target, target_repo=target_repo)

        def read() -> _Reached:
            graph = _target_graph(
                env, target=target, ref=ref, target_repo=repo, direction=command
            ).graph
            return _Reached(graph, reach(graph, relation))

        graph, rows = _answer(env, options, command, read, empty=lambda reached: not reached.rows)
        _report_warnings(graph.warnings)
        _report_depth_stops(graph, depth_limit)
        emit(
            _table_paths([model(**hit.row.model_dump()) for hit in rows], fmt),
            fmt=fmt,
            columns=columns,
            empty=f"No {noun} found{_within(depth_limit)}.",
        )


def _emit_uses(
    target: str,
    *,
    ref: str | None,
    target_repo: str | None,
    depth: str | None,
    names: list[str],
    fmt: FormatOption,
    columns: list[str] | None,
    options: GraphSourceOptions,
) -> None:
    """Print one row per place a dependent of ``target`` defines, sets or reads one of ``names``.

    A dependent the index holds no analysis for is a failure line (exit 1),
    never an absence of uses.
    """
    depth_limit = _parse_depth(depth or "unlimited")
    with report_errors(), ExitStack() as stack:
        env = _graph_env(
            stack, options, command="impact", depth=depth_limit, live=False, all_refs=False
        )
        repo = _require_repo(env, target, target_repo=target_repo)

        def read() -> _Reached:
            graph = _target_graph(
                env, target=target, ref=ref, target_repo=repo, direction="impact"
            ).graph
            return _Reached(graph, reach(graph, "impacts"))

        graph, rows = _answer(env, options, "impact", read, empty=lambda reached: not reached.rows)
        _report_warnings(graph.warnings)
        _report_depth_stops(graph, depth_limit)
        commits = {
            (hit.row.repo, sha) for hit in rows if hit.row.repo is not None for sha in hit.shas
        }
        facts = env.sqlite_index.variable_facts(commits, names)
        _hint_undefined(env, target=target, repo=repo, ref=ref, names=names)
        skipped = skipped_files(rows, facts)
        if skipped:
            _diag.warning(
                f"{plural(len(skipped), 'file')} in the dependents could not be read for "
                f"variables: {'; '.join(skipped)}"
            )
        missing = unanalysed(rows, facts)
        # With a dependent unanalysed, "no dependents use it" is not known.
        none = f"No dependents use {' or '.join(names)}{_within(depth_limit)}."
        emit(
            _table_keys(dependent_uses(rows, facts, names), fmt),
            fmt=fmt,
            columns=columns,
            empty=None if missing else none,
            table_columns=(("variable", *VariableUseHit.table_columns) if len(names) > 1 else None),
        )
        if missing:
            _report_unanalysed(env, missing, [hit.row for hit in rows if hit.row.repo is not None])
        finish(bool(missing))


_VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _variable_names(values: list[str]) -> list[str]:
    """``values`` deduplicated, each checked to be a valid Ansible variable name."""
    for value in values:
        if not _VARIABLE_NAME.fullmatch(value) or keyword.iskeyword(value):
            raise_usage(f"--uses takes a variable name, not {q(value)}")
    return list(dict.fromkeys(values))


def _hint_undefined(
    env: _GraphEnv, *, target: str, repo: str, ref: str | None, names: list[str]
) -> None:
    """Hint when ROLE (indexed, or a local checkout) defines none of ``names``: a typo, likely.

    Silent when what ROLE defines is unknown (not indexed, or not analysed).
    """
    label = f"{repo}@{ref}" if ref else repo
    local = Path(target).expanduser()
    if local.is_dir():
        from untaped_ansible.domain.variables import defines  # noqa: PLC0415  # Jinja2

        defined = defines(_role_variable_files(local)) & set(names)
        label = target
    else:
        shas = env.sqlite_index.ref_shas(
            [selection.key for selection in env.graph_source.selections], repo, ref
        )
        found = env.sqlite_index.variable_facts({(repo, sha) for sha in shas}, names)
        if not shas or found.unanalysed:
            return
        defined = {
            fact.name
            for commit_facts in found.facts.values()
            for fact in commit_facts
            if fact.how == "defines"
        }
    if not defined:
        _diag.hint(
            f"{label} defines no variable {' or '.join(names)} (defaults, vars, argument_specs)"
        )


def _role_variable_files(root: Path) -> dict[str, str]:
    """The files of a local checkout that can define variables, relative path → text."""
    from untaped_ansible.domain.variables import selected  # noqa: PLC0415  # Jinja2

    files: dict[str, str] = {}
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = sorted(name for name in subdirectories if name not in _UNWALKED)
        base = Path(directory)
        parts = set(base.relative_to(root).parts)
        if not {"defaults", "vars", "meta"} & parts:
            continue
        for name in sorted(names):
            relative = (base / name).relative_to(root).as_posix()
            if selected(relative) != "yaml":
                continue
            try:
                files[relative] = (base / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return dict(sorted(files.items()))


#: Directories of a local checkout that never hold a role's variables.
_UNWALKED = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".tox"})


def _report_unanalysed(env: _GraphEnv, missing: list[str], rows: list[ReachedRow]) -> None:
    """One failure naming the dependents with no variable facts, and how to fill them in."""
    shown = ", ".join(missing[:5])
    more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
    _diag.failure(
        AnsibleError(
            f"{plural(len(missing), 'dependent')} without variable facts, so their uses are "
            f"unknown: {shown}{more} (their last refresh failed or did not reach them)",
            category="not_found",
        )
    )
    selections = env.graph_source.selections
    if not env.graph_source.saved:
        _diag.hint("re-run this command with --refresh")
        return
    holding = env.sqlite_index.selecting_sources(selection.key for selection in selections)
    labels = {f"{row.repo}@{row.ref}" if row.ref else str(row.repo): row for row in rows}
    involved = {
        key
        for label in missing
        if (row := labels.get(label)) is not None and row.repo is not None
        for key in holding.get(repo_key(row.repo), set())
    }
    for selection in selections:
        if selection.key in involved or not involved:
            _diag.hint(run_hint(f"ansible source refresh {shlex.quote(selection.label)}"))


def _table_keys(hits: list[VariableUseHit], fmt: FormatOption) -> list[VariableUseHit]:
    """``hits`` with each key path rendered dotted in a table (one segment); others keep it."""
    if fmt != "table":
        return hits
    return [
        hit.model_copy(update={"key_path": [render_key_path(hit.key_path)] if hit.key_path else []})
        for hit in hits
    ]


def _table_paths[R: (ReachedRow, DependencyMatch)](rows: list[R], fmt: FormatOption) -> list[R]:
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


def _report_warnings(warnings: Iterable[str]) -> None:
    """Print warnings on stderr."""
    for warning in warnings:
        _diag.warning(warning)


_DIRECTIONS: dict[str, GraphDirection] = {"up": "impact", "down": "deps", "both": "both"}

_REACH_OUTPUT: dict[str, tuple[EdgeRelation, type[ReachedRow], str]] = {
    "deps": ("requires", ReachedDependency, "dependencies"),
    "impact": ("impacts", ReachedDependent, "dependents"),
}
"""Per task command: the edges it walks, its record model and its row noun."""


def _report_depth_stops(graph: DependencyGraph, depth: int | None, *, root: str = "") -> None:
    """Hint on stderr how many nodes the depth limit left unread, and how to read them."""
    count = sum(1 for node in graph.nodes if node.stopped == "depth")
    if depth is None or not count:
        return
    prefix = f"{root}: " if root else ""
    them = "it" if count == 1 else "them"
    _diag.hint(
        f"{prefix}{plural(count, 'repo')} not read beyond --depth {depth}; "
        f"pass --depth unlimited to read {them}"
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

    Nothing is refreshed yet (see :func:`_answer`), so arguments can be
    checked against it first.
    """
    ctx = app_context()
    settings = get_config_section("ansible", AnsibleSettings)
    has_source = bool(
        options.source or options.orgs or options.teams or options.repos or settings.default_source
    )
    flags = [
        flag
        for flag, given in (
            ("--refresh", options.refresh is True),
            ("--cached", options.refresh is False),
            ("--backend", options.backend is not None),
        )
        if given
    ]
    if flags and not has_source:
        raise UsageError(
            f"{flags[0]} needs a source; select one with --source NAME or inline selectors "
            "(or set ansible.default_source)"
        )
    if options.backend is not None and options.refresh is False:
        raise UsageError("--backend needs a refresh; drop --cached")
    aliases = AliasRepository().entries()
    github_settings = load_github_settings()
    github_host = github_web_host(github_settings.api_url)
    graph_source = _graph_source(options, default_source=settings.default_source)
    # Live reads resolve only the default branch; every ref exists only in a source's cache.
    if all_refs and live:
        raise UsageError("--all-refs reads cached source data; drop --live")
    if all_refs and not graph_source.selections:
        raise UsageError(
            "--all-refs reads cached source data; select one with --source NAME "
            "(or set ansible.default_source)"
        )
    sqlite_index = open_index(settings)
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


def _answer[T](
    env: _GraphEnv,
    options: GraphSourceOptions,
    direction: GraphDirection,
    read: Callable[[], T],
    *,
    empty: Callable[[T], bool],
) -> T:
    """``read()``, over the selected sources refreshed first when ``--refresh`` or their age asks.

    A command that reads no source's data (none selected, or ``--live``
    reading downstream only) just reads.
    """
    if not _reads_source(env, direction):
        return read()
    wiring = RefreshWiring(
        index=env.sqlite_index,
        aliases=env.aliases,
        settings=env.settings,
        github_settings=env.github_settings,
        http=app_context().http,
        concurrency=clamp_parallel(
            options.parallel or env.settings.git_fetch_parallel,
            cap=GIT_PARALLEL_CAP,
            policy="Git fetch limit",
        ),
        backend=options.backend,
    )
    try:
        served = serve_sources(
            env.graph_source.selections,
            refresh=options.refresh,
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


def _reads_source(env: _GraphEnv, direction: GraphDirection) -> bool:
    """Whether a build in ``direction`` reads the selected sources' data.

    Only ``deps --live`` reads nothing of them: ``graph --live`` still reads
    its upstream half (``impact``) from the index.
    """
    if not env.graph_source.selections:
        return False
    return not (env.live and direction == "deps")


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
                concurrency=self._settings.probe_parallel,
            )
        return self._index


def _target_graph(
    env: _GraphEnv,
    *,
    target: str,
    ref: str | None,
    target_repo: str | None,
    direction: GraphDirection,
) -> _BuiltGraph:
    """Build one root's graph, with its warnings attached, and say what it read."""
    target_repo_name = _require_repo(env, target, target_repo=target_repo)
    graph_source = env.graph_source
    direction, graph_warnings = _effective_direction(source_state=graph_source, direction=direction)
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


class _Reached(NamedTuple):
    """What ``deps`` or ``impact`` read: the graph, and the rows it reached."""

    graph: DependencyGraph
    rows: list[Reach]


@dataclass
class _Found:
    """What ``find`` read: each root's graph by its label, and the matches."""

    graphs: list[tuple[str, DependencyGraph]] = field(default_factory=list)
    matches: list[DependencyMatch] = field(default_factory=list)


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
class _GraphSource:
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
        selections: list[SelectedSource] = []
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
                SelectedSource(
                    definition=source,
                    key=source_commands._saved_source_key(source_name),
                    label=source_name,
                    subject=f"source {source_name}",
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
        label = _inline_label(source)
        return _GraphSource(
            selections=(SelectedSource(definition=source, key=key, label=label, subject=label),),
            key=key,
            label=label,
            saved=False,
        )
    return _GraphSource(selections=(), key=None, label=None, saved=False)


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


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


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
    *, source_state: _GraphSource, direction: GraphDirection
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


def _refresh_hint(source_state: _GraphSource) -> str | None:
    """Compose the exact fix command surfaced in missing-ref warnings."""
    if not source_state.selections:
        return None
    if source_state.saved:
        commands = _source_refresh_commands(source_state.selections)
        return f"Run {commands} to update it."
    return "Re-run this command with `--refresh` to update it."


def _source_refresh_commands(selections: tuple[SelectedSource, ...]) -> str:
    """Join the exact `untaped ansible source refresh NAME` commands for selections."""
    return " and ".join(
        f"`untaped ansible source refresh {selection.label}`" for selection in selections
    )


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
        tree = graph_tree(graph, header_note=header_note)
        if output is None:
            print_tree(tree, ui=ui)
            return
        rendered = tree_text(tree, ui=ui)
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
