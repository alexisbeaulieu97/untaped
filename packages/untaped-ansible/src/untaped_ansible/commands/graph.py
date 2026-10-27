"""``deps``, ``impact``, ``find`` and ``graph``: Ansible dependency answers over a source or GitHub.

``deps``, ``impact`` (with ``impact uses``) and ``find`` answer one question
each and print rows (``ansible.dependency``, ``ansible.dependent``,
``ansible.variable_use_hit``, ``ansible.dependency_match``); ``graph`` renders
the whole graph as a tree, Mermaid or JSON document, both directions at once
by default. All of them report warnings on stderr, share the source data
options (:class:`~untaped_ansible.commands._source_data.SourceData`) and fall
back to ``ansible.default_source`` when no source is selected.
"""

from __future__ import annotations

import keyword
import os
import re
import shlex
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal, NamedTuple, cast

from pydantic import Field

from untaped.sdk import (
    Group,
    Output,
    Positional,
    Record,
    Short,
    Stdin,
    UsageError,
    echo,
    plural,
    print_tree,
    run_hint,
    tree_text,
    ui_context,
)
from untaped_ansible.commands._graph import (
    ALL_REFS,
    LIVE,
    TARGET,
    TARGET_REPO,
    GraphCommand,
    GraphDirection,
    GraphEnv,
    check_all_refs,
    parse_depth,
    require_repo,
    within,
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
from untaped_ansible.domain.identity import repo_key
from untaped_ansible.domain.reach import Reach, reach
from untaped_ansible.domain.renderers import graph_tree, render_graph
from untaped_ansible.domain.variable_queries import (
    dependent_uses,
    render_key_path,
    skipped_files,
    unanalysed,
)
from untaped_ansible.errors import AnsibleError
from untaped_ansible.records import (
    Dependency,
    DependencyMatch,
    Dependent,
    ReachedRow,
    VariableUseHit,
)


class _Reaching(GraphCommand):
    """A command over the repositories reached from ROLE in one direction."""

    role: Positional[str] = Field(description=TARGET)
    target_repo: str | None = Field(None, description=TARGET_REPO)

    def reached(
        self, direction: Literal["deps", "impact"], *, ref: str | None, live: bool, all_refs: bool
    ) -> list[Reach]:
        """Each repository reached, after the warnings and the depth hint on stderr."""
        relation: EdgeRelation = "requires" if direction == "deps" else "impacts"
        check_all_refs(ref, all_refs=all_refs)
        with ExitStack() as stack:
            env = self.env(stack, command=direction, live=live, all_refs=all_refs)
            repo = require_repo(env, self.role, target_repo=self.target_repo)

            def read() -> _Reached:
                graph = self.target_graph(
                    env, target=self.role, ref=ref, target_repo=repo, direction=direction
                ).graph
                return _Reached(graph, reach(graph, relation))

            graph, rows = self.answer(env, direction, read, empty=lambda found: not found.rows)
            self.report_warnings(graph.warnings)
            self.report_depth_stops(graph, env.depth)
            return rows


class Deps(_Reaching):
    """Show what ROLE depends on (downstream), one row per repository per ROLE ref.

    Reads a source's data when one is selected (--source, inline selectors,
    or ansible.default_source), refreshed first when older than its max age,
    and GitHub live otherwise (or with --live). Each row carries the ref as declared, the shortest
    path from ROLE and the ROLE ref it was reached from; each repo appears
    once per ROLE ref. `untaped ansible --help` explains refs, local targets
    and where a walk stops.

    Examples
    --------
    ```console
    $ untaped ansible deps acme/web --ref v2.1.0
    $ untaped ansible deps ./roles/web --target-repo acme/web --format json
    ```
    """

    ref: str | None = Field(
        None, description="Branch, tag, or SHA of ROLE; omit for its default branch."
    )
    all_refs: bool = Field(False, description=ALL_REFS)
    live: bool = Field(False, description=LIVE)
    output: Output[Dependency]

    def run(self) -> Iterator[Dependency]:
        rows = self.reached("deps", ref=self.ref, live=self.live, all_refs=self.all_refs)
        self.output.empty(f"No dependencies found{within(parse_depth(self.depth))}.")
        table = self.output.format == "table"
        for hit in rows:
            yield _table_path(Dependency(**hit.row.model_dump()), table=table)


class Impact(_Reaching):
    """Show what depends on ROLE (upstream), one row per repository per root ref.

    Reads a source's data (--source, inline selectors, or
    ansible.default_source), refreshed first when older than its max age.
    Each row carries the ref it
    declares, its shortest path to ROLE and the ROLE ref it was reached from
    (`root_ref` tells a dependent's pinned refs apart). `impact uses` asks
    which of them use a variable of ROLE instead.

    Examples
    --------
    ```console
    $ untaped ansible impact acme/base --source platform
    $ untaped ansible impact acme/base --ref main --format pipe
    ```
    """

    ref: str | None = Field(
        None,
        description="Branch, tag, or SHA of ROLE to find dependents of; omit for every cached ref.",
    )
    output: Output[Dependent]

    def run(self) -> Iterator[Dependent]:
        rows = self.reached("impact", ref=self.ref, live=False, all_refs=False)
        self.output.empty(f"No dependents found{within(parse_depth(self.depth))}.")
        table = self.output.format == "table"
        for hit in rows:
            yield _table_path(Dependent(**hit.row.model_dump()), table=table)


class Uses(_Reaching):
    """Show where ROLE's dependents use a variable: one row per place.

    Keeps the dependents that use NAME (any of several), one row per place:
    defines (a top-level key under defaults/ or vars/, or an argument_specs
    option), sets (any other YAML key of that name, register or loop_var; a
    module argument of that name counts too) or reads (a Jinja expression).
    INI inventories and CI config are not read. Each place is read at the
    commit the source indexed; a dependent with no row does not mention it.

    Dependents whose refresh failed have no variable facts: an error names
    them and how to refresh them (`source refresh NAME`, or --refresh for
    inline selectors), and the command exits 1. A hint that ROLE defines no
    such variable usually means a typo.

    Examples
    --------
    ```console
    $ untaped ansible impact uses acme/base base_port --format json
    ```
    """

    name: Positional[list[str]] = Field(
        description="Variable to look for; give several for any of them."
    )
    ref: str | None = Field(
        None,
        description="Branch, tag, or SHA of ROLE to find dependents of; omit for every cached ref.",
    )
    output: Output[VariableUseHit]

    def run(self) -> Iterator[VariableUseHit]:
        names = _variable_names(self.name)
        depth = parse_depth(self.depth)
        with ExitStack() as stack:
            env = self.env(stack, command="impact", live=False, all_refs=False)
            repo = require_repo(env, self.role, target_repo=self.target_repo)

            def read() -> _Reached:
                graph = self.target_graph(
                    env, target=self.role, ref=self.ref, target_repo=repo, direction="impact"
                ).graph
                return _Reached(graph, reach(graph, "impacts"))

            graph, rows = self.answer(env, "impact", read, empty=lambda found: not found.rows)
            self.report_warnings(graph.warnings)
            self.report_depth_stops(graph, depth)
            commits = {
                (hit.row.repo, sha) for hit in rows if hit.row.repo is not None for sha in hit.shas
            }
            facts = env.sqlite_index.variable_facts(commits, names)
            self._hint_undefined(env, repo=repo, names=names)
            skipped = skipped_files(rows, facts)
            if skipped:
                self.ctx.diag.warning(
                    f"{plural(len(skipped), 'file')} in the dependents could not be read for "
                    f"variables: {'; '.join(skipped)}"
                )
            missing = unanalysed(rows, facts)
            if not missing:
                # With a dependent unanalysed, "no dependents use it" is not known.
                self.output.empty(f"No dependents use {' or '.join(names)}{within(depth)}.")
            if len(names) > 1:
                self.output.columns[:] = ["variable", *self.output.columns]
            table = self.output.format == "table"
            for hit in dependent_uses(rows, facts, names):
                yield _table_key(hit) if table else hit
            if missing:
                self._report_unanalysed(env, missing, [hit.row for hit in rows if hit.row.repo])

    def _hint_undefined(self, env: GraphEnv, *, repo: str, names: list[str]) -> None:
        """Hint when ROLE (indexed, or a local checkout) defines none of ``names``: a typo, likely.

        Silent when what ROLE defines is unknown (not indexed, or not analysed).
        """
        label = f"{repo}@{self.ref}" if self.ref else repo
        local = Path(self.role).expanduser()
        if local.is_dir():
            from untaped_ansible.domain.variables import defines  # noqa: PLC0415  # Jinja2

            defined = defines(_role_variable_files(local)) & set(names)
            label = self.role
        else:
            shas = env.sqlite_index.ref_shas(
                [selection.key for selection in env.graph_source.selections], repo, self.ref
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
            self.ctx.diag.hint(
                f"{label} defines no variable {' or '.join(names)} (defaults, vars, argument_specs)"
            )

    def _report_unanalysed(self, env: GraphEnv, missing: list[str], rows: list[ReachedRow]) -> None:
        """One failure naming the dependents with no variable facts, and how to fill them in."""
        shown = ", ".join(missing[:5])
        more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
        self.ctx.diag.failure(
            AnsibleError(
                f"{plural(len(missing), 'dependent')} without variable facts, so their uses are "
                f"unknown: {shown}{more} (their last refresh failed or did not reach them)",
                category="not_found",
            )
        )
        selections = env.graph_source.selections
        if not env.graph_source.saved:
            self.ctx.diag.hint("re-run this command with --refresh")
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
                self.ctx.diag.hint(
                    run_hint(f"ansible source refresh {shlex.quote(selection.label)}")
                )


class ImpactGroup(Group):
    """What depends on a role: its dependents, or where they use its variables."""

    runs = Impact
    commands = (Uses,)


class Find(GraphCommand):
    """Find which roots contain a repository downstream, one row per match.

    Roots come from --root, or from --stdin: `owner/repo@ref` lines, or pipe
    records with scm_url/repo_url/repo/full_name and effective_scm_ref/ref.
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

    target: Positional[list[str]] = Field(
        description=(
            "Repository to find: owner/repo, GitHub URL, or source alias; give several "
            "to find any of them."
        )
    )
    root: list[str] = Field(
        default_factory=list,
        description="Root to search as owner/repo[@ref], GitHub URL, source alias, or local path.",
    )
    stdin: Stdin[Record]
    all_refs: bool = Field(False, description=ALL_REFS)
    live: bool = Field(False, description=LIVE)
    output: Output[DependencyMatch]

    def run(self) -> Iterator[DependencyMatch]:
        if self.stdin.requested and self.root:
            raise UsageError("pass --root or --stdin, not both")
        if not self.stdin.requested and not self.root:
            raise UsageError("provide --root or --stdin")
        depth = parse_depth(self.depth)
        with ExitStack() as stack:
            inputs = (
                self._piped_roots()
                if self.stdin.requested
                else list(dict.fromkeys((root_from_line(r), RootInput()) for r in self.root))
            )
            env = self.env(stack, command="find", live=self.live, all_refs=self.all_refs)
            wanted = [require_repo(env, repo) for repo in self.target]
            resolved = [(root, source, require_repo(env, root.target)) for root, source in inputs]

            def read() -> _Found:
                # Spellings of one repo (URL, .git, SSH, alias) share one graph build.
                graphs: dict[tuple[str, str | None, str | None], DependencyGraph] = {}
                found = _Found()
                for root, source, repo in resolved:
                    local = root.target if Path(root.target).expanduser().exists() else None
                    key = (repo_key(repo), root.ref, local)
                    graph = graphs.get(key)
                    if graph is None:
                        graph = graphs[key] = self.target_graph(
                            env,
                            target=root.target,
                            ref=root.ref,
                            target_repo=repo,
                            direction="deps",
                        ).graph
                        found.graphs.append((_root_label(root), graph))
                    found.matches.extend(find_matches(graph, wanted, source=source))
                return found

            found = self.answer(env, "deps", read, empty=lambda found: not found.matches)
        for label, graph in found.graphs:
            for warning in graph.warnings:
                self.ctx.diag.warning(f"{label}: {warning}")
            self.report_depth_stops(graph, depth, root=label)
        self.output.empty(f"No matching roots found{within(depth)}.")
        table = self.output.format == "table"
        for match in found.matches:
            yield _table_path(match, table=table)

    def _piped_roots(self) -> list[tuple[GraphRoot, RootInput]]:
        """Roots from stdin (bare ``owner/repo@ref`` lines or pipe records), with their input.

        Identical inputs collapse; records naming one root stay one entry each.
        """
        roots: list[tuple[GraphRoot, RootInput]] = []
        for number, value in enumerate(self.stdin.values(), 1):
            if isinstance(value, str):
                roots.append((root_from_line(value), RootInput()))
                continue
            record = value.model_dump(mode="json")
            root = root_from_record(record)
            if root is None:
                raise UsageError(
                    f"line {number}: record has no repository field ({', '.join(REPO_FIELDS)})"
                )
            roots.append((root, input_from_record(self.stdin.kind(value), record)))
        return list(dict.fromkeys(roots))


Direction = Literal["up", "down", "both"]

_DIRECTIONS: dict[Direction, GraphDirection] = {"up": "impact", "down": "deps", "both": "both"}
_WRITTEN = ("tree", "mermaid", "json")  # the formats --out writes


class GraphDocument(DependencyGraph):
    """A dependency graph as ``graph`` prints it: the tree, Mermaid or the JSON document.

    The JSON document has `target_id`, `nodes` (each with `stopped` as on
    rows), `edges`, `cycles` and `warnings`. An edge's `id` is the same for
    every declaration of one dependency, so duplicates collapse to one edge. A
    cycle has `kind`, `relation`, `node_ids` and `edge_ids`: `cycle` is a
    closed ordered path, `scc_group` a sorted set of nodes and their internal
    edges, used when a component has too many cycles to list. Node ids are
    lowercase; labels keep the display casing.
    """

    header: str = Field("", exclude=True)
    """What the tree's first line says after the target: the data read and the depth."""


def _tree(document: GraphDocument) -> None:
    print_tree(graph_tree(document, header_note=document.header), ui=ui_context(strict=False))


def _mermaid(document: GraphDocument) -> None:
    echo(render_graph(document, "mermaid"))


def _json(document: GraphDocument) -> None:
    echo(render_graph(document, "json"))


class Graph(GraphCommand):
    """Render the Ansible dependency graph of a role, repo, or playbook.

    For task-shaped answers as rows, use `deps`, `impact` or `find`. Inline
    source selectors (--org, --team, --repo, --path, --ref-kind,
    --ref-pattern, --ref-scan-default) are cached under a deterministic
    fingerprint key, so repeated identical invocations reuse the same scan.
    --parallel defaults to ansible.git_fetch_parallel and is capped at 32.

    The tree's first line names the target, the data it read and the depth;
    a "used by" section lists dependents and a "depends on" section
    dependencies. Each repo line names the file that declares the edge, plus
    `unpinned` (no version) or `pins X` (the declared version differs
    from the resolved ref). A shared subtree prints once, numbered `[n]`;
    a later occurrence holds one `… see [n]` line instead of its branches,
    and a repo already on the path says `↻ cycle`
    (`(cycle)` in ASCII); a stopped repo is marked `…` with a
    `not read:` note. The summary counts repos, edges, cycles (a component
    with too many cycles to list counts as one cyclic group), unresolved
    dependencies and stopped repos. Lines too wide for the terminal end in
    `…`. Warnings go to stderr, even with --out.

    Examples
    --------
    ```console
    $ untaped ansible graph acme/base --org acme --team acme/platform --direction up --refresh
    $ untaped ansible graph acme/app --source prod --depth 2
    $ untaped ansible graph ./roles/web --target-repo acme/web --direction down
    ```
    """

    target: Positional[str] = Field(description=TARGET)
    ref: str | None = Field(
        None,
        description=(
            "Target branch, tag, or SHA; omit for its default branch (and every ref upstream)."
        ),
    )
    direction: Direction = Field(
        "both",
        description=(
            "up: what depends on TARGET (requires a source); down: what TARGET "
            "depends on; both (default)."
        ),
    )
    all_refs: bool = Field(False, description=ALL_REFS)
    target_repo: str | None = Field(None, description=TARGET_REPO)
    live: bool = Field(False, description=LIVE)
    out: Annotated[Path | None, Short("-o")] = Field(
        None, description="Write the graph to this file instead of stdout."
    )
    output: Output[GraphDocument] = Output(
        render={"tree": _tree, "mermaid": _mermaid, "json": _json}, format="tree"
    )

    def run(self) -> GraphDocument | None:
        check_all_refs(self.ref, all_refs=self.all_refs)
        if self.out is not None and self.output.format not in _WRITTEN:
            raise UsageError("--out writes the tree, mermaid or json format")
        direction = _DIRECTIONS[self.direction]
        with ExitStack() as stack:
            env = self.env(stack, command="graph", live=self.live, all_refs=self.all_refs)
            repo = require_repo(env, self.target, target_repo=self.target_repo)
            built = self.answer(
                env,
                direction,
                lambda: self.target_graph(
                    env, target=self.target, ref=self.ref, target_repo=repo, direction=direction
                ),
                empty=lambda built: not built.graph.edges,
            )
        self.report_warnings(built.graph.warnings)
        self.report_depth_stops(built.graph, env.depth)
        depth = "unlimited depth" if env.depth is None else f"depth {env.depth}"
        document = GraphDocument(**dict(built.graph), header=f"{built.data_source} · {depth}")
        if self.out is None:
            return document
        if self.output.format == "tree":
            text = tree_text(
                graph_tree(document, header_note=document.header), ui=ui_context(strict=False)
            )
        else:
            text = render_graph(document, "mermaid" if self.output.format == "mermaid" else "json")
        path = self.out.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return None


class _Reached(NamedTuple):
    """What ``deps`` or ``impact`` read: the graph, and the rows it reached."""

    graph: DependencyGraph
    rows: list[Reach]


@dataclass
class _Found:
    """What ``find`` read: each root's graph by its label, and the matches."""

    graphs: list[tuple[str, DependencyGraph]] = field(default_factory=list)
    matches: list[DependencyMatch] = field(default_factory=list)


_VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Directories of a local checkout that never hold a role's variables.
_UNWALKED = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".tox"})


def _variable_names(values: list[str]) -> list[str]:
    """``values`` deduplicated, each checked to be a valid Ansible variable name."""
    for value in values:
        if not _VARIABLE_NAME.fullmatch(value) or keyword.iskeyword(value):
            raise UsageError(f"NAME takes a variable name, not {value!r}")
    return list(dict.fromkeys(values))


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


def _table_key(hit: VariableUseHit) -> VariableUseHit:
    """``hit`` with its key path rendered dotted, one segment, as a table shows it."""
    return hit.model_copy(
        update={"key_path": [render_key_path(hit.key_path)] if hit.key_path else []}
    )


def _table_path[R: ReachedRow | DependencyMatch](row: R, *, table: bool) -> R:
    """``row`` with its path cut to ``first → … → last`` in a table; other formats keep it.

    The short path stays a one-item list, so each row keeps its record type.
    """
    if not table:
        return row
    path = row.path
    short = " → ".join(path if len(path) <= 2 else [path[0], "…", path[-1]])
    return cast("R", row.model_copy(update={"path": [short]}))


def _root_label(root: GraphRoot) -> str:
    return f"{root.target}@{root.ref}" if root.ref else root.target
