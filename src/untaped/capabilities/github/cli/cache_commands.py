"""Cyclopts sub-app: ``untaped github cache``."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Literal

from cyclopts import Parameter

from untaped.capabilities.github.application import (
    RepositoryInventoryItem,
    RepositoryInventoryScope,
)
from untaped.capabilities.github.cli._client import corpus_auth_header, open_client
from untaped.capabilities.github.cli.scopes import (
    ArchivedOption,
    CorpusParallelOption,
    DepthOption,
    OrgOption,
    RepoOption,
    TeamOption,
    parse_team_scopes,
    read_stdin_repos,
)
from untaped.capabilities.github.domain import CorpusRepoResult
from untaped.capabilities.github.settings import GithubSettings
from untaped.capability_api import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    OutputFormat,
    StdinOption,
    UsageError,
    YesOption,
    app_context,
    batch_apply,
    clamp_parallel,
    create_app,
    echo,
    emit,
    finish,
    plural,
    report_errors,
    summary,
)

AllOption = Annotated[
    bool,
    Parameter(name="--all", negative="", help="Select every cached repository."),
]
PruneOption = Annotated[
    bool,
    Parameter(name="--prune", negative="", help="Clean departed or archived repos in scope."),
]
FilterOrgOption = Annotated[
    list[str] | None,
    Parameter(
        name="--org",
        help="Only cached repositories owned by this org. Repeatable.",
        consume_multiple=False,
        negative="",
    ),
]

app = create_app(name="cache", help="Inspect and manage the local Git corpus cache.")


@app.command(name="status")
def status_command(
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List repositories cached in the local corpus."""
    from untaped.capabilities.github.application import StatusCorpus  # noqa: PLC0415
    from untaped.capabilities.github.infrastructure import GitCorpusCache  # noqa: PLC0415

    with report_errors():
        settings = app_context().section("github", GithubSettings)
        rows = StatusCorpus(GitCorpusCache())(root=settings.corpus_path)
        records = [row.model_dump() for row in rows]
        emit(
            _status_display(records) if fmt == "table" and not columns else records,
            fmt=fmt,
            columns=columns,
            kind="github.corpus_repo",
            empty="No repositories are cached in the local corpus.",
        )
        _status_summary(rows)


@app.command(name="sync")
def sync_command(
    *,
    org: OrgOption = None,
    team: TeamOption = None,
    repo: RepoOption = None,
    stdin: StdinOption = False,
    archived: ArchivedOption = False,
    refs: Annotated[
        Literal["default", "branches", "tags", "all"],
        Parameter(name="--refs", help="Ref profile to fetch."),
    ] = "default",
    ref: Annotated[
        list[str] | None,
        Parameter(
            name="--ref",
            help="Additional ref glob to fetch. Repeatable.",
            consume_multiple=False,
            negative="",
        ),
    ] = None,
    refresh: Annotated[
        bool,
        Parameter(
            name="--refresh",
            negative="",
            help=(
                "Fetch every repo. Default: fetch copies older than "
                "github.sweep.max_age_seconds that GitHub reports as pushed since."
            ),
        ),
    ] = False,
    depth: DepthOption = 1,
    parallel: CorpusParallelOption | None = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Fetch repositories into the local corpus without a query, so later sweeps start warm."""
    from untaped.capabilities.github.application import (  # noqa: PLC0415
        CorpusSyncOptions,
        ResolveRepositoryInventory,
        SyncCorpus,
    )
    from untaped.capabilities.github.domain import RefSelector  # noqa: PLC0415
    from untaped.capabilities.github.infrastructure import GitCorpusCache  # noqa: PLC0415

    with report_errors():
        settings = app_context().section("github", GithubSettings)
        stdin_repos, stdin_items = read_stdin_repos() if stdin else ((), ())
        orgs = tuple(org or ())
        teams = parse_team_scopes(team, orgs=orgs)
        repos = tuple(repo or ())
        if not (orgs or teams or repos or stdin_repos or stdin_items):
            raise UsageError("cache sync requires --org, --team, --repo, or --stdin")
        options = CorpusSyncOptions(
            scope=RepositoryInventoryScope(orgs=orgs, teams=teams, repos=repos),
            stdin_repos=stdin_repos,
            stdin_items=stdin_items,
            include_archived=archived,
            refs=RefSelector(profile=refs, globs=tuple(ref or ())),
            refresh=refresh,
            max_age_seconds=settings.sweep.max_age_seconds,
            depth=depth,
            parallel=clamp_parallel(
                parallel if parallel is not None else settings.sweep.sync_concurrency,
                cap=32,
                policy="Git corpus worker cap",
            ),
        )
        with open_client() as (client, ui), ui.progress("Syncing repositories…") as progress:
            outcomes = SyncCorpus(
                inventory=ResolveRepositoryInventory(client),
                corpus=GitCorpusCache(),
                root=settings.corpus_path,
                auth_header=corpus_auth_header(settings),
            )(options, progress=progress)
        emit(
            [outcome.model_dump(mode="json") for outcome in outcomes],
            fmt=fmt,
            columns=columns,
            kind="github.sync_outcome",
            empty="No repositories in scope.",
        )
        for outcome in outcomes:
            if outcome.failed:
                ui.message("error", f"{outcome.repo}: {outcome.error}")
        echo(summary("sync", Counter(outcome.action for outcome in outcomes)), err=True)
        finish(any(outcome.failed for outcome in outcomes))


@app.command(name="delete")
def delete_command(
    repos: Annotated[
        list[str] | None,
        Parameter(help="Cached repositories (OWNER/NAME) to delete.", negative=""),
    ] = None,
    /,
    *,
    all_repos: AllOption = False,
    org: FilterOrgOption = None,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Delete cached repositories from the managed local corpus."""
    with report_errors():
        names = tuple(repos or ())
        if names and all_repos:
            raise UsageError("pass REPO arguments or --all, not both")
        if not names and not all_repos:
            raise UsageError("cache delete requires REPO arguments or --all")
        _delete(
            _select(names, all_repos=all_repos, prune=False, org=org),
            yes=yes,
            dry_run=dry_run,
            fmt=fmt,
            columns=columns,
        )


@app.command(name="prune")
def prune_command(
    *,
    org: Annotated[
        list[str] | None,
        Parameter(
            name="--org",
            help="Org whose departed or archived cached repos to delete. Repeatable; required.",
            consume_multiple=False,
            negative="",
        ),
    ] = None,
    yes: YesOption = False,
    dry_run: DryRunOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Delete cached repositories that left or were archived in their org."""
    with report_errors():
        if not org:
            raise UsageError("cache prune requires --org")
        _delete(
            _select((), all_repos=False, prune=True, org=org),
            yes=yes,
            dry_run=dry_run,
            fmt=fmt,
            columns=columns,
        )


@app.command(name="clean")
def clean_command(
    *,
    repo: RepoOption = None,
    all_repos: AllOption = False,
    prune: PruneOption = False,
    org: FilterOrgOption = None,
    yes: YesOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Deprecated: use ``cache delete`` or ``cache prune``; removed in 7.0."""
    with report_errors():
        app_context().ui(strict=False).message(
            "warning",
            "`cache clean` is deprecated and will be removed in 7.0; "
            "use `cache delete` or `cache prune`",
        )
        repos = tuple(repo or ())
        if sum(bool(value) for value in (repos, all_repos, prune)) != 1:
            raise UsageError("cache clean requires exactly one of --repo, --all, or --prune")
        if prune and not org:
            raise UsageError("cache clean --prune requires --org")
        _delete(
            _select(repos, all_repos=all_repos, prune=prune, org=org),
            yes=yes,
            dry_run=False,
            fmt=fmt,
            columns=columns,
        )


def _select(
    repos: tuple[str, ...],
    *,
    all_repos: bool,
    prune: bool,
    org: list[str] | None,
) -> tuple[CorpusRepoResult, ...]:
    """Pick the cached repositories an explicit list, ``--all`` or a prune selects."""
    from untaped.capabilities.github.application import (  # noqa: PLC0415
        ResolveRepositoryInventory,
    )
    from untaped.capabilities.github.infrastructure import GitCorpusCache  # noqa: PLC0415

    settings = app_context().section("github", GithubSettings)
    cached = _in_orgs(GitCorpusCache().list_repos(root=settings.corpus_path), orgs=tuple(org or ()))
    if prune:
        with open_client() as (client, ui), ui.progress("Resolving repository inventory…"):
            live = ResolveRepositoryInventory(client)(
                RepositoryInventoryScope(orgs=tuple(org or ()))
            )
        return _departed_or_archived(cached, live)
    if all_repos:
        return cached
    requested = {name.casefold() for name in repos}
    return tuple(row for row in cached if row.repo.casefold() in requested)


def _delete(
    selected: tuple[CorpusRepoResult, ...],
    *,
    yes: bool,
    dry_run: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    """Confirm, then delete ``selected`` from the corpus and emit the removed rows."""
    from untaped.capabilities.github.application import CleanCorpus  # noqa: PLC0415
    from untaped.capabilities.github.infrastructure import GitCorpusCache  # noqa: PLC0415

    ctx = app_context()
    settings = ctx.section("github", GithubSettings)
    cleaner = CleanCorpus(GitCorpusCache())
    outcome = batch_apply(
        selected,
        lambda row: cleaner(root=settings.corpus_path, repo=row),
        verb="delete",
        noun="cached GitHub repo",
        label=lambda row: row.repo,
        describe=lambda row: {"repo": row.repo, "ref": row.ref, "path": row.path},
        ui=ctx.ui(strict=False),
        destructive=True,
        assume_yes=yes,
        preview_only=dry_run,
    )
    removed = selected if dry_run else tuple(row for _, row in outcome.results)
    emit(
        [row.model_dump() for row in removed],
        fmt=fmt,
        columns=columns,
        kind="github.corpus_repo",
        empty="No matching repositories were cached.",
    )
    finish(outcome)


@app.command(name="worktree")
def worktree_command(
    repo: Annotated[str, Parameter(help="Repository OWNER/NAME.")],
    /,
    *,
    ref: Annotated[str | None, Parameter(name="--ref", help="Cached ref to materialize.")] = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Materialize one cached repo/ref and print the worktree path."""
    from untaped.capabilities.github.application import WorktreeCorpus  # noqa: PLC0415
    from untaped.capabilities.github.infrastructure import GitCorpusCache  # noqa: PLC0415

    with report_errors():
        ctx = app_context()
        ui = ctx.ui()
        settings = ctx.section("github", GithubSettings)
        with ui.progress("Materializing worktree…"):
            result = WorktreeCorpus(GitCorpusCache())(
                repo,
                root=settings.corpus_path,
                ref=ref,
            )
        emit(result, fmt=fmt, columns=columns, kind="github.worktree")


def _status_summary(rows: tuple[CorpusRepoResult, ...]) -> None:
    total = _human_size(sum(row.disk_bytes for row in rows))
    ages = sorted(age for row in rows if (age := _parse_time(row.fetched_at)) is not None)
    oldest = _relative_age(ages[0]) if ages else "n/a"
    newest = _relative_age(ages[-1]) if ages else "n/a"
    echo(f"Cache: {plural(len(rows), 'repo')}, {total}, oldest {oldest}, newest {newest}", err=True)


def _status_display(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Table rows with a readable size and fetch age; other formats keep raw values."""
    return [
        {
            "repo": record["repo"],
            "ref": record["ref"],
            "profile": record["profile"],
            "archived": record["archived"],
            "size": _human_size(int(str(record["disk_bytes"]))),
            "fetched": _relative_age(_parse_time(record["fetched_at"])),
            "path": record["path"],
        }
        for record in records
    ]


def _human_size(size: int) -> str:
    """Render a byte count with binary units: ``512 B``, ``1.5 KiB``, ``2.0 GiB``."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{size} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _relative_age(value: datetime | None) -> str:
    """Render how long ago ``value`` was: ``just now``, ``5 minutes ago``, ``3 days ago``."""
    if value is None:
        return "n/a"
    seconds = int((datetime.now(UTC) - value).total_seconds())
    if seconds < 60:
        return "just now"
    for unit, span in (("day", 86400), ("hour", 3600)):
        if seconds >= span:
            return f"{plural(seconds // span, unit)} ago"
    return f"{plural(seconds // 60, 'minute')} ago"


def _in_orgs(
    cached: tuple[CorpusRepoResult, ...], *, orgs: tuple[str, ...]
) -> tuple[CorpusRepoResult, ...]:
    """Keep cached repos owned by one of ``orgs`` (all when none); owners are case-insensitive."""
    if not orgs:
        return cached
    owners = {org.casefold() for org in orgs}
    return tuple(row for row in cached if row.repo.partition("/")[0].casefold() in owners)


def _departed_or_archived(
    cached: tuple[CorpusRepoResult, ...],
    live: tuple[RepositoryInventoryItem, ...],
) -> tuple[CorpusRepoResult, ...]:
    live_by_name = {row.full_name: row for row in live}
    return tuple(
        row for row in cached if row.repo not in live_by_name or live_by_name[row.repo].archived
    )
