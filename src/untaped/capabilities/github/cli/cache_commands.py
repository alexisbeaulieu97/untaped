"""Cyclopts sub-app: ``untaped github cache``."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

from untaped.api import (
    ColumnsOption,
    ConfigError,
    FormatOption,
    app_context,
    batch_apply,
    create_app,
    echo,
    emit,
    finish,
    report_errors,
)
from untaped.capabilities.github.application import (
    RepositoryInventoryItem,
    RepositoryInventoryScope,
)
from untaped.capabilities.github.cli._client import open_client
from untaped.capabilities.github.cli._scopes import OrgOption, TeamOption
from untaped.capabilities.github.domain import CorpusRepoResult
from untaped.capabilities.github.settings import GithubSettings

RepoOption = Annotated[
    list[str] | None,
    Parameter(name="--repo", help="Repository owner/name. Repeatable.", consume_multiple=False),
]
AllOption = Annotated[
    bool,
    Parameter(name="--all", negative="", help="Clean every cached repository."),
]
PruneOption = Annotated[
    bool,
    Parameter(name="--prune", negative="", help="Clean departed or archived repos in scope."),
]
YesOption = Annotated[
    bool,
    Parameter(name=["--yes", "-y"], negative="", help="Confirm destructive clean operations."),
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
        emit(
            [row.model_dump() for row in rows],
            fmt=fmt,
            columns=columns,
            kind="github.corpus_repo",
            empty="No repositories are cached in the local corpus.",
        )
        _status_summary(rows)


@app.command(name="clean")
def clean_command(
    *,
    repo: RepoOption = None,
    all_repos: AllOption = False,
    prune: PruneOption = False,
    org: OrgOption = None,
    team: TeamOption = None,
    yes: YesOption = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Remove repositories from the managed local corpus."""
    from untaped.capabilities.github.application import (  # noqa: PLC0415
        CleanCorpus,
        ResolveRepositoryInventory,
    )
    from untaped.capabilities.github.infrastructure import GitCorpusCache  # noqa: PLC0415

    with report_errors():
        repos = tuple(repo or ())
        _require_one_clean_mode(repos=repos, all_repos=all_repos, prune=prune)
        settings = app_context().section("github", GithubSettings)
        corpus = GitCorpusCache()
        cached = corpus.list_repos(root=settings.corpus_path)
        if prune:
            selected = _prune_selection(cached, org=org, team=team)
            with open_client() as (client, ui), ui.progress("Resolving repository inventory…"):
                live = ResolveRepositoryInventory(client)(_prune_scope(org=org, team=team))
            selected = _departed_or_archived(selected, live)
        elif all_repos:
            selected = cached
        else:
            requested = set(repos)
            selected = tuple(row for row in cached if row.repo in requested)

        cleaner = CleanCorpus(corpus)
        ui = app_context().ui(strict=False)
        outcome = batch_apply(
            selected,
            lambda row: cleaner(root=settings.corpus_path, repo=row),
            verb="delete",
            noun="cached GitHub repo",
            label=lambda row: row.repo,
            describe=lambda row: {"repo": row.repo, "ref": row.ref, "path": row.path},
            ui=ui,
            destructive=True,
            assume_yes=yes,
        )
        rows = [removed.model_dump() for _, removed in outcome.results]
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="github.corpus_repo",
            empty="No matching repositories were cached.",
        )
        finish(outcome)


@app.command(name="worktree")
def worktree_command(
    repo: Annotated[str, Parameter(help="Repository owner/name.")],
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
    total = sum(row.disk_bytes for row in rows)
    dates = sorted(row.fetched_at for row in rows if row.fetched_at)
    if dates:
        echo(
            f"Cache: {len(rows)} repos, {total} bytes, oldest {dates[0]}, newest {dates[-1]}",
            err=True,
        )
    else:
        echo(f"Cache: {len(rows)} repos, {total} bytes, oldest n/a, newest n/a", err=True)


def _require_one_clean_mode(
    *,
    repos: tuple[str, ...],
    all_repos: bool,
    prune: bool,
) -> None:
    selected = sum(bool(value) for value in (repos, all_repos, prune))
    if selected != 1:
        raise ConfigError("cache clean requires exactly one of --repo, --all, or --prune")


def _prune_scope(*, org: list[str] | None, team: list[str] | None) -> RepositoryInventoryScope:
    orgs = tuple(org or ())
    if team:
        raise ConfigError(
            "--prune cannot resolve team membership from the corpus; prune with --org or --repo"
        )
    if not orgs:
        raise ConfigError("cache clean --prune requires --org")
    return RepositoryInventoryScope(orgs=orgs)


def _prune_selection(
    cached: tuple[CorpusRepoResult, ...],
    *,
    org: list[str] | None,
    team: list[str] | None,
) -> tuple[CorpusRepoResult, ...]:
    scope = _prune_scope(org=org, team=team)
    orgs = set(scope.orgs)
    return tuple(row for row in cached if any(row.repo.startswith(f"{org}/") for org in orgs))


def _departed_or_archived(
    cached: tuple[CorpusRepoResult, ...],
    live: tuple[RepositoryInventoryItem, ...],
) -> tuple[CorpusRepoResult, ...]:
    live_by_name = {row.full_name: row for row in live}
    return tuple(
        row for row in cached if row.repo not in live_by_name or live_by_name[row.repo].archived
    )
