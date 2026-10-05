"""Cyclopts sub-app: ``untaped github cache``."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Literal

from cyclopts import Parameter

from untaped.sdk import (
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
    not_found,
    plural,
    report_errors,
    report_row_errors,
    summary,
    writes,
)
from untaped_github.application import (
    RepositoryInventoryItem,
    RepositoryInventoryScope,
)
from untaped_github.cli._client import corpus_auth_header, open_client
from untaped_github.cli.scopes import (
    ArchivedOption,
    CorpusParallelOption,
    DepthOption,
    OrgOption,
    RepoOption,
    TeamOption,
    org_scope,
    parse_team_scopes,
    read_stdin_repos,
)
from untaped_github.domain import CorpusRepoResult, github_web_host
from untaped_github.errors import GithubError
from untaped_github.settings import GithubSettings

AllOption = Annotated[
    bool,
    Parameter(name="--all", negative="", help="Select every cached repository."),
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
    from untaped_github.application import StatusCorpus  # noqa: PLC0415
    from untaped_github.infrastructure import GitCorpusCache  # noqa: PLC0415

    with report_errors():
        settings = app_context().section("github", GithubSettings)
        rows = StatusCorpus(GitCorpusCache(auth_host=None))(root=settings.cache_dir)
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
    archived: ArchivedOption = "exclude",
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
    from untaped_github.application import (  # noqa: PLC0415
        CorpusSyncOptions,
        ResolveRepositoryInventory,
        SyncCorpus,
    )
    from untaped_github.domain import RefSelector  # noqa: PLC0415
    from untaped_github.infrastructure import GitCorpusCache  # noqa: PLC0415

    with report_errors():
        settings = app_context().section("github", GithubSettings)
        stdin_repos, stdin_items = read_stdin_repos() if stdin else ((), ())
        repos = tuple(repo or ())
        piped = bool(stdin_repos or stdin_items)
        orgs = org_scope(org, scoped=bool(team or repos or piped))
        teams = parse_team_scopes(team, orgs=orgs)
        if not (orgs or teams or repos or piped):
            raise UsageError(
                "cache sync requires --org, --team, --repo, --stdin, "
                "or a github.default_org setting"
            )
        options = CorpusSyncOptions(
            scope=RepositoryInventoryScope(orgs=orgs, teams=teams, repos=repos),
            stdin_repos=stdin_repos,
            stdin_items=stdin_items,
            archived=archived,
            refs=RefSelector(profile=refs, globs=tuple(ref or ())),
            refresh=refresh,
            max_age_seconds=settings.sweep.max_age_seconds,
            depth=depth,
            parallel=clamp_parallel(
                parallel if parallel is not None else settings.sweep.parallel,
                cap=32,
                policy="Git corpus worker cap",
            ),
        )
        with open_client() as (client, ui), ui.progress("Syncing repositories…") as progress:
            outcomes = SyncCorpus(
                inventory=ResolveRepositoryInventory(client),
                corpus=GitCorpusCache(auth_host=github_web_host(settings.base_url)),
                root=settings.cache_dir,
                auth_header=corpus_auth_header(settings),
            )(options, progress=progress)
        emit(
            outcomes,
            fmt=fmt,
            columns=columns,
            kind="github.sync_outcome",
            empty="No repositories in scope.",
        )
        report_row_errors(outcomes, item=lambda outcome: outcome.repo)
        echo(summary("sync", Counter(outcome.action for outcome in outcomes)), err=True)
        finish(any(outcome.failed for outcome in outcomes))


@app.command(name="delete")
@writes(destructive=True)
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
@writes(destructive=True)
def prune_command(
    *,
    org: Annotated[
        list[str] | None,
        Parameter(
            name="--org",
            help=(
                "Org whose departed or archived cached repos to delete. Repeatable; "
                "defaults to github.default_org."
            ),
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
        orgs = org_scope(org, scoped=False)
        if not orgs:
            raise UsageError("cache prune requires --org or a github.default_org setting")
        _delete(
            _select((), all_repos=False, prune=True, org=list(orgs)),
            yes=yes,
            dry_run=dry_run,
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
    from untaped_github.application import (  # noqa: PLC0415
        ResolveRepositoryInventory,
    )
    from untaped_github.infrastructure import GitCorpusCache  # noqa: PLC0415

    settings = app_context().section("github", GithubSettings)
    cached = _in_orgs(
        GitCorpusCache(auth_host=None).list_repos(root=settings.cache_dir), orgs=tuple(org or ())
    )
    if prune:
        with open_client() as (client, ui), ui.progress("Resolving repository inventory…"):
            live = ResolveRepositoryInventory(client)(
                RepositoryInventoryScope(orgs=tuple(org or ()))
            )
        return _departed_or_archived(cached, live)
    if all_repos:
        return cached
    known = {row.repo.casefold() for row in cached}
    missing = [name for name in repos if name.casefold() not in known]
    if missing:
        raise GithubError("; ".join(not_found("cached repo", name) for name in missing))
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
    from untaped_github.application import (  # noqa: PLC0415
        CleanCorpus,
        with_disk_bytes,
    )
    from untaped_github.infrastructure import GitCorpusCache  # noqa: PLC0415

    ctx = app_context()
    settings = ctx.section("github", GithubSettings)
    cleaner = CleanCorpus(GitCorpusCache(auth_host=None))
    outcome = batch_apply(
        selected,
        # Measured just before deleting: each row reports the space it frees.
        lambda row: cleaner(root=settings.cache_dir, repo=with_disk_bytes(row)),
        verb="delete",
        noun="cached GitHub repo",
        label=lambda row: row.repo,
        describe=lambda row: {"repo": row.repo, "ref": row.ref, "path": row.path},
        ui=ctx.ui(strict=False),
        destructive=True,
        assume_yes=yes,
        preview_only=dry_run,
    )
    removed = (
        tuple(with_disk_bytes(row) for row in selected)
        if dry_run
        else tuple(row for _, row in outcome.results)
    )
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
    from untaped_github.application import WorktreeCorpus  # noqa: PLC0415
    from untaped_github.infrastructure import GitCorpusCache  # noqa: PLC0415

    with report_errors():
        ctx = app_context()
        ui = ctx.ui()
        settings = ctx.section("github", GithubSettings)
        with ui.progress("Materializing worktree…"):
            result = WorktreeCorpus(GitCorpusCache(auth_host=None))(
                repo,
                root=settings.cache_dir,
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
