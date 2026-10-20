"""Cyclopts sub-app: ``untaped github cache``."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Literal

from cyclopts import Parameter

from untaped.sdk import (
    ColumnsOption,
    Diagnostics,
    DryRunOption,
    FormatOption,
    OutputFormat,
    StdinOption,
    UsageError,
    YesOption,
    app_context,
    batch_apply,
    check_columns,
    clamp_parallel,
    create_app,
    emit,
    finish,
    not_found,
    plural,
    report_errors,
    size_text,
    summary,
    writes,
)
from untaped_github.application import RepositoryInventoryScope
from untaped_github.cli._client import open_client, open_corpus
from untaped_github.cli.scopes import (
    ArchivedOption,
    CorpusParallelOption,
    OrgOption,
    RepoOption,
    TeamOption,
    org_scope,
    parse_team_scopes,
    read_stdin_repos,
)
from untaped_github.errors import GithubError
from untaped_github.records import CorpusRepoResult, GithubRepo, WorktreeResult
from untaped_github.settings import GithubSettings

_diag = Diagnostics()

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
    """List repositories cached in the local corpus.

    One row per repo github has stored, then the count, total size and
    freshness spread on stderr. ``path`` is the repo in the git plugin's
    repo store, which other plugins may share.
    """
    from untaped_github.application import StatusCorpus  # noqa: PLC0415

    with report_errors():
        settings = app_context().section("github", GithubSettings)
        rows = StatusCorpus(open_corpus(settings))()
        emit(
            _status_display([row.model_dump() for row in rows])
            if fmt == "table" and not columns
            else rows,
            fmt=fmt,
            columns=columns,
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
    parallel: CorpusParallelOption | None = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Fetch repositories into the local corpus without a query, so later sweeps start warm.

    Run it before a batch of sweeps, or on a schedule. It picks repos to
    fetch as ``sweep`` does, and each row says what became of its repo; a
    failed row exits 1, or 5 when a fetch timed out.

    Examples
    --------
    ```console
    $ untaped github cache sync --org acme
    ```
    """
    from untaped_github.application import (  # noqa: PLC0415
        CorpusSyncOptions,
        ResolveRepositoryInventory,
        SyncCorpus,
    )
    from untaped_github.domain import RefSelector  # noqa: PLC0415

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
            parallel=clamp_parallel(
                parallel if parallel is not None else settings.sweep.parallel,
                cap=32,
                policy="Git corpus worker cap",
            ),
        )
        with open_client() as (client, _), _diag.progress("Syncing repositories") as progress:
            outcomes = SyncCorpus(
                inventory=ResolveRepositoryInventory(client),
                corpus=open_corpus(settings),
            )(options, progress=progress)
        emit(
            outcomes,
            fmt=fmt,
            columns=columns,
            empty="No repositories in scope.",
        )
        for outcome in outcomes:
            if outcome.error is not None:
                _diag.failure(outcome.error, item=outcome.repo)
        _diag.info(summary("sync", Counter(outcome.action for outcome in outcomes)))
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
    """Delete cached repositories from the corpus; a repo another plugin uses stays for it.

    untaped github never changes GitHub: this releases github's part of each
    repo, its refs, its ``untaped-github.json`` and its worktrees. A repo
    nothing else uses is ``removed``; one that a workspace, ansible or a
    hand-added worktree still uses stays as ``released``, and ``kept`` says
    who kept it. ``--all`` selects every repo github has stored, and
    ``--org`` narrows the selection; there is no ``--team``, because github
    doesn't record team membership. A named repo that isn't cached exits 1
    before anything is deleted. The next sweep that covers a deleted repo
    fetches it again.

    Examples
    --------
    ```console
    $ untaped github cache delete acme/api --dry-run
    ```
    """
    with report_errors():
        check_columns(CorpusRepoResult, columns=columns, fmt=fmt)
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
    """Delete cached repositories that left or were archived in their org (as `cache delete`).

    Examples
    --------
    ```console
    $ untaped github cache prune --org acme --dry-run
    ```
    """
    with report_errors():
        check_columns(CorpusRepoResult, columns=columns, fmt=fmt)
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

    settings = app_context().section("github", GithubSettings)
    cached = _in_orgs(open_corpus(settings).list_repos(), orgs=tuple(org or ()))
    if prune:
        with open_client() as (client, _), _diag.progress("Resolving repository inventory"):
            live = ResolveRepositoryInventory(client)(
                RepositoryInventoryScope(orgs=tuple(org or ()))
            )
        return _departed_or_archived(cached, live)
    if all_repos:
        return cached
    known = {row.full_name.casefold() for row in cached}
    missing = [name for name in repos if name.casefold() not in known]
    if missing:
        raise GithubError("; ".join(not_found("cached repo", name) for name in missing))
    requested = {name.casefold() for name in repos}
    return tuple(row for row in cached if row.full_name.casefold() in requested)


def _delete(
    selected: tuple[CorpusRepoResult, ...],
    *,
    yes: bool,
    dry_run: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    """Confirm, then release ``selected`` from the corpus and emit what became of each.

    A repo nobody else uses is ``removed`` (``disk_bytes`` freed); one another
    plugin or a hand-added worktree still uses is ``released`` (``kept`` says who).
    """
    from untaped_github.application import (  # noqa: PLC0415
        CleanCorpus,
        with_disk_bytes,
    )

    ctx = app_context()
    settings = ctx.section("github", GithubSettings)
    cleaner = CleanCorpus(open_corpus(settings))
    outcome = batch_apply(
        selected,
        lambda row: cleaner(repo=row),
        verb="delete",
        noun="cached GitHub repo",
        label=lambda row: row.full_name,
        describe=lambda row: {"repo": row.full_name, "ref": row.ref, "path": row.path},
        ui=ctx.ui(strict=False),
        destructive=True,
        assume_yes=yes,
        preview_only=dry_run,
    )
    released = (
        [with_disk_bytes(row) for row in selected]
        if dry_run
        else [row for _, row in outcome.results]
    )
    emit(
        _released_display([row.model_dump() for row in released])
        if not dry_run and fmt == "table" and not columns
        else released,
        fmt=fmt,
        columns=columns,
        # A preview's table shows what each repo would free.
        table_columns=(*CorpusRepoResult.table_columns, "disk_bytes") if dry_run else None,
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
    """Materialize one cached repo/ref and print the worktree path.

    It checks a stored ref out, detached, under
    ``~/.untaped/plugins/github/worktrees/``, for reading a repo's files;
    ``untaped workspace`` makes checkouts you edit. It works only for refs
    already stored, and offline once their files have been fetched.
    ``--format raw`` prints just the path, for ``$(…)``.
    """
    from untaped_github.application import WorktreeCorpus  # noqa: PLC0415

    with report_errors():
        check_columns(WorktreeResult, columns=columns, fmt=fmt)
        settings = app_context().section("github", GithubSettings)
        with _diag.progress("Materializing worktree"):
            result = WorktreeCorpus(open_corpus(settings))(repo, ref=ref)
        emit(result, fmt=fmt, columns=columns)


def _status_summary(rows: tuple[CorpusRepoResult, ...]) -> None:
    total = size_text(sum(row.disk_bytes for row in rows))
    ages = sorted(age for row in rows if (age := _parse_time(row.fetched_at)) is not None)
    oldest = _relative_age(ages[0]) if ages else "n/a"
    newest = _relative_age(ages[-1]) if ages else "n/a"
    _diag.info(f"Cache: {plural(len(rows), 'repo')}, {total}, oldest {oldest}, newest {newest}")


def _status_display(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Table rows with a readable size and fetch age; other formats keep raw values."""
    return [
        {
            "repo": record["full_name"],
            "ref": record["ref"],
            "profile": record["profile"],
            "archived": record["archived"],
            "size": size_text(int(str(record["disk_bytes"]))),
            "fetched": _relative_age(_parse_time(record["fetched_at"])),
            "path": record["path"],
        }
        for record in records
    ]


def _released_display(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Table rows of a delete: the repo, what became of it, and who kept it or what it freed."""
    return [
        {
            "repo": record["full_name"],
            "status": record["status"],
            "detail": (
                f"{size_text(int(str(record['disk_bytes'])))} freed"
                if record["status"] == "removed"
                else f"kept: {record['kept']}"
            ),
        }
        for record in records
    ]


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
    return tuple(row for row in cached if row.full_name.partition("/")[0].casefold() in owners)


def _departed_or_archived(
    cached: tuple[CorpusRepoResult, ...],
    live: tuple[GithubRepo, ...],
) -> tuple[CorpusRepoResult, ...]:
    live_by_name = {row.full_name: row for row in live}
    return tuple(
        row
        for row in cached
        if row.full_name not in live_by_name or live_by_name[row.full_name].archived
    )
