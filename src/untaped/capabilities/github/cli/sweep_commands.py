"""Cyclopts command: ``untaped github sweep``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from cyclopts import Parameter, validators

from untaped.api import (
    ColumnsOption,
    ConfigError,
    FormatOption,
    OutputFormat,
    app_context,
    clamp_parallel,
    echo,
    emit,
    finish,
    read_identifiers,
    report_errors,
)
from untaped.capabilities.github.application import RepositoryInventoryScope
from untaped.capabilities.github.cli._client import open_client
from untaped.capabilities.github.cli._scopes import OrgOption, TeamOption, parse_team_scopes
from untaped.capabilities.github.settings import GithubSettings

if TYPE_CHECKING:
    from untaped.capabilities.github.application import GitCorpus, SweepMatch, SweepReport
    from untaped.capabilities.github.domain import RepoSweepOutcome, SweepQuery

RepoOption = Annotated[
    list[str] | None,
    Parameter(name="--repo", help="Repository owner/name. Repeatable.", consume_multiple=False),
]
StdinOption = Annotated[
    bool,
    Parameter(name="--stdin", negative="", help="Read repository full_name values from stdin."),
]
DepthOption = Annotated[
    int,
    Parameter(
        name="--depth",
        validator=validators.Number(gte=0),
        help="Git fetch depth; 0 is full.",
    ),
]
ParallelOption = Annotated[
    int | None,
    Parameter(name=["--parallel", "-j"], help="Parallel Git workers (capped at 32)."),
]


def sweep_command(
    *,
    org: OrgOption = None,
    team: TeamOption = None,
    repo: RepoOption = None,
    stdin: StdinOption = False,
    archived: Annotated[bool, Parameter(name="--archived", negative="")] = False,
    grep: Annotated[
        list[str] | None,
        Parameter(name="--grep", help="Content regex. Repeatable.", consume_multiple=False),
    ] = None,
    not_grep: Annotated[
        list[str] | None,
        Parameter(
            name="--not-grep", help="Content regex that must not match.", consume_multiple=False
        ),
    ] = None,
    path: Annotated[
        list[str] | None,
        Parameter(
            name="--path", help="Git pathspec for content predicates.", consume_multiple=False
        ),
    ] = None,
    has_file: Annotated[
        list[str] | None,
        Parameter(name="--has-file", help="File glob that must exist.", consume_multiple=False),
    ] = None,
    lacks_file: Annotated[
        list[str] | None,
        Parameter(
            name="--lacks-file", help="File glob that must not exist.", consume_multiple=False
        ),
    ] = None,
    any_mode: Annotated[bool, Parameter(name="--any", negative="")] = False,
    ignore_case: Annotated[bool, Parameter(name=["--ignore-case", "-i"], negative="")] = False,
    fixed_strings: Annotated[bool, Parameter(name=["--fixed-strings", "-F"], negative="")] = False,
    word_regexp: Annotated[bool, Parameter(name=["--word-regexp", "-w"], negative="")] = False,
    refs: Annotated[
        Literal["default", "branches", "tags", "all"],
        Parameter(name="--refs", help="Ref profile to sweep."),
    ] = "default",
    ref: Annotated[
        list[str] | None,
        Parameter(name="--ref", help="Additional ref glob. Repeatable.", consume_multiple=False),
    ] = None,
    sync: Annotated[
        bool | None,
        Parameter(name="--sync", negative="--no-sync", help="Force sync or use cache only."),
    ] = None,
    show: Annotated[
        Literal["repos", "matches"],
        Parameter(name="--show", help="Report repo rows or deduped match rows."),
    ] = "repos",
    owners: Annotated[bool, Parameter(name="--owners", negative="--no-owners")] = True,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
    strict: Annotated[bool, Parameter(name="--strict", negative="")] = False,
    fail_on_match: Annotated[bool, Parameter(name="--fail-on-match", negative="")] = False,
    depth: DepthOption = 1,
    parallel: ParallelOption = None,
) -> None:
    """Sweep repository refs for content and file-presence predicates."""
    from untaped.capabilities.github.application import (  # noqa: PLC0415
        ResolveRepositoryInventory,
        Sweep,
        SweepOptions,
    )
    from untaped.capabilities.github.domain import RefSelector, SweepQuery  # noqa: PLC0415
    from untaped.capabilities.github.infrastructure import GitCorpusCache  # noqa: PLC0415
    from untaped.capabilities.github.infrastructure.git_corpus import (  # noqa: PLC0415
        git_auth_header,
    )

    with report_errors():
        ctx = app_context()
        settings = ctx.section("github", GithubSettings)
        stdin_repos = tuple(read_identifiers([], stdin=True, id_field="full_name")) if stdin else ()
        scope = _scope(org=org, team=team, repo=repo, stdin_repos=stdin_repos)
        query = SweepQuery(
            greps=tuple(grep or ()),
            not_greps=tuple(not_grep or ()),
            paths=tuple(path or ()),
            has_files=tuple(has_file or ()),
            lacks_files=tuple(lacks_file or ()),
            any_mode=any_mode,
            ignore_case=ignore_case,
            fixed_strings=fixed_strings,
            word_regexp=word_regexp,
            refs=RefSelector(profile=refs, globs=tuple(ref or ())),
        )
        _validate_query(query)
        workers = _parallel(parallel if parallel is not None else settings.sweep.sync_concurrency)
        corpus = GitCorpusCache()
        _validate_content_patterns(corpus, settings, query)

        sync_mode: Literal["auto", "force", "off"]
        sync_mode = "auto" if sync is None else "force" if sync else "off"
        options = SweepOptions(
            scope=scope,
            stdin_repos=stdin_repos,
            include_archived=archived,
            query=query,
            sync=sync_mode,
            max_age_seconds=settings.sweep.max_age_seconds,
            depth=depth,
            parallel=workers,
            owners=owners,
        )

        if sync_mode == "off":
            report = Sweep(
                inventory=lambda _scope: (),
                corpus=corpus,
                root=settings.corpus_path,
                auth_header=lambda: None,
            )(options)
        else:
            with open_client() as (client, ui):
                token = _token(settings)
                with ui.progress("Sweeping repositories…"):
                    report = Sweep(
                        inventory=ResolveRepositoryInventory(client),
                        corpus=corpus,
                        root=settings.corpus_path,
                        auth_header=lambda: git_auth_header(token) if token else None,
                    )(options)

        if show == "matches":
            rows = _match_records(report.matches)
            emit(
                rows, fmt=fmt, columns=columns, kind="github.sweep_match", empty="No matches found."
            )
        else:
            rows = _repo_records(report.rows)
            emit(
                _display_rows(rows, query=query, owners=owners, fmt=fmt, columns=columns),
                fmt=fmt,
                columns=columns or _default_columns(query=query, owners=owners),
                kind="github.sweep_repo",
                empty="No matching repositories found.",
            )
        _footer(report)
        finish((strict and bool(report.unscanned)) or (fail_on_match and bool(report.rows)))


def _scope(
    *,
    org: list[str] | None,
    team: list[str] | None,
    repo: list[str] | None,
    stdin_repos: tuple[str, ...],
) -> RepositoryInventoryScope:
    orgs = tuple(org or ())
    team_scopes = parse_team_scopes(team, orgs=orgs)
    repos = tuple(repo or ())
    if not orgs and not team_scopes and not repos and not stdin_repos:
        raise ConfigError("sweep requires --org, --team, --repo, or --stdin")
    return RepositoryInventoryScope(orgs=orgs, teams=team_scopes, repos=repos)


def _validate_query(query: SweepQuery) -> None:
    try:
        query.validate()
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _validate_content_patterns(
    corpus: GitCorpus,
    settings: GithubSettings,
    query: SweepQuery,
) -> None:
    paths = query.paths
    fixed_strings = query.fixed_strings
    for flag, pattern in (
        *[("--grep", pattern) for pattern in query.greps],
        *[("--not-grep", pattern) for pattern in query.not_greps],
    ):
        error = corpus.validate_pattern(
            root=settings.corpus_path,
            pattern=pattern,
            paths=paths,
            fixed_strings=fixed_strings,
        )
        if error is None:
            continue
        path = _path_from_error(paths, error)
        if path is not None:
            raise ConfigError(f"--path {path!r}: {error}")
        raise ConfigError(f"{flag} {pattern!r}: {error}")


def _path_from_error(paths: tuple[str, ...], error: str) -> str | None:
    return next((path for path in paths if path in error), None)


def _parallel(value: int) -> int:
    if value < 1:
        raise ConfigError("--parallel must be positive")
    return clamp_parallel(value, cap=32, policy="Git corpus worker cap")


def _token(settings: GithubSettings) -> str:
    if settings.token is None:
        return ""
    return settings.token.get_secret_value().strip()


def _repo_records(rows: tuple[RepoSweepOutcome, ...]) -> list[dict[str, object]]:
    return [
        {
            "full_name": row.full_name,
            "clone_url": row.clone_url,
            "refs_matched": list(row.refs_matched),
            "hits": dict(row.hits),
            "owners": list(row.owners),
            "synced_at": row.synced_at,
        }
        for row in rows
    ]


def _match_records(rows: tuple[SweepMatch, ...]) -> list[dict[str, object]]:
    return [
        {
            "full_name": row.full_name,
            "refs": list(row.refs),
            "path": row.path,
            "line": row.line,
            "text": row.text,
        }
        for row in rows
    ]


def _display_rows(
    rows: list[dict[str, object]],
    *,
    query: SweepQuery,
    owners: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> list[dict[str, object]]:
    if fmt != "table" or columns:
        return rows
    labels = query.labels()
    display: list[dict[str, object]] = []
    for row in rows:
        hits = row["hits"]
        display_row: dict[str, object] = {"full_name": row["full_name"]}
        for label in labels:
            display_row[label] = hits.get(label, 0)  # type: ignore[attr-defined]
        if query.refs.beyond_default():
            display_row["refs_matched"] = ",".join(row["refs_matched"])  # type: ignore[arg-type]
        if owners:
            display_row["owners"] = ",".join(row["owners"])  # type: ignore[arg-type]
        display.append(display_row)
    return display


def _default_columns(*, query: SweepQuery, owners: bool) -> list[str] | None:
    if not query.refs.beyond_default() and owners:
        return None
    columns = ["full_name", *query.labels()]
    if query.refs.beyond_default():
        columns.append("refs_matched")
    if owners:
        columns.append("owners")
    return columns


def _footer(report: SweepReport) -> None:
    oldest = report.oldest_fetched_at.isoformat() if report.oldest_fetched_at else "n/a"
    echo(
        (
            f"Sweep: {len(report.rows)} matched of {report.scanned} scanned "
            f"({report.refreshed} refreshed, {report.cached} cached), oldest fetch {oldest}"
        ),
        err=True,
    )
    if report.unscanned:
        for failure in report.unscanned:
            echo(f"warning: unscanned {failure.repo}: {failure.reason}", err=True)
