"""Cyclopts sub-app: ``untaped github search``.

Four subcommands, one per GitHub search endpoint. Each builds a frozen
filter object from CLI flags, hands it to its use case, and pipes the
result through core's ``emit`` helper. Composition lives here;
the use cases own the orchestration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from cyclopts import Parameter, validators

from untaped.sdk import (
    ColumnsOption,
    FormatOption,
    StdinOption,
    create_app,
    emit,
    plural,
    read_identifiers,
    report_errors,
)
from untaped_github.cli._client import open_client
from untaped_github.cli.scopes import (
    REPO_KINDS,
    ArchivedOption,
    OrgOption,
    RepoOption,
    TeamOption,
    org_scope,
    parse_team_scopes,
)

if TYPE_CHECKING:
    from untaped.sdk import UiContext

# Shared across all four search subcommands. GitHub-specific (the
# 1000-result cap belongs to GitHub, not untaped), so it lives
# here rather than in untaped's option aliases.
SearchLimitOption = Annotated[
    int,
    Parameter(
        name="--limit",
        validator=validators.Number(gte=1),
        help=(
            "Cap result count. GitHub enforces a hard 1000-result cap "
            "on search; pass --limit 1000 to opt into the maximum."
        ),
    ),
]
FreeTextArgument = Annotated[str | None, Parameter(help="Free-text query (passed verbatim).")]
UserOption = Annotated[
    str | None,
    Parameter(
        name="--user",
        help="user:LOGIN. Without any scope: github.default_org, else @me.",
    ),
]
LanguageOption = Annotated[
    str | None, Parameter(name="--language", help="Match the language (language:X).")
]

app = create_app(
    name="search",
    help="Search GitHub for repos, code, issues, and users.",
)


# GitHub serves at most this many results per search, so no row past it
# can reveal that a --limit truncated the output.
_GITHUB_SEARCH_CAP = 1000
# Search pages hold at most this many rows; a probe row past a full page
# would cost a whole extra request against the per-minute search budget.
_GITHUB_SEARCH_PAGE = 100


def _repo_scopes(values: list[str] | None, *, stdin: bool) -> tuple[str, ...]:
    """Merge explicit ``--repo`` values with optional stdin repo scopes."""
    repos = list(values or ())
    if stdin:
        repos.extend(
            read_identifiers([], stdin=True, id_field="full_name", accept_kinds=REPO_KINDS)
        )
    return tuple(repos)


def _probe(limit: int) -> int:
    """Ask for one row past ``limit`` so :func:`_cap` can tell whether more match.

    Skipped where it would need an extra request: at GitHub's cap and on a page boundary.
    """
    if limit >= _GITHUB_SEARCH_CAP or limit % _GITHUB_SEARCH_PAGE == 0:
        return limit
    return limit + 1


def _cap[T](rows: list[T], limit: int, ui: UiContext) -> list[T]:
    """Keep ``limit`` rows; note on stderr when the probe row shows more match."""
    if len(rows) > limit:
        ui.message(
            "info",
            f"showing the first {plural(limit, 'result')}; more match, raise --limit to see them",
        )
    return rows[:limit]


@app.command(name="repos")
def repos_command(
    query: FreeTextArgument = None,
    /,
    *,
    user: UserOption = None,
    org: OrgOption = None,
    team: TeamOption = None,
    repo: RepoOption = None,
    stdin: StdinOption = False,
    name: Annotated[
        str | None,
        Parameter(name="--name", help="Match against repo name (in:name)."),
    ] = None,
    language: LanguageOption = None,
    archived: ArchivedOption = "exclude",
    fork: Annotated[
        bool | None,
        Parameter(name="--fork", negative="--no-fork", help="Only forks; --no-fork excludes them."),
    ] = None,
    visibility: Annotated[
        Literal["public", "private"] | None,
        Parameter(name="--visibility", help="Only public or only private repos."),
    ] = None,
    sort: Annotated[
        Literal["stars", "forks", "help-wanted-issues", "updated"] | None,
        Parameter(name="--sort", help="Sort order; best match when omitted."),
    ] = None,
    limit: SearchLimitOption = 30,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Search repositories (``GET /search/repositories``)."""
    from untaped_github.application import SearchRepos  # noqa: PLC0415
    from untaped_github.domain import RepoSearchFilters  # noqa: PLC0415
    from untaped_github.domain.models import REPO_HIT_COLUMNS  # noqa: PLC0415

    with report_errors():
        repos = _repo_scopes(repo, stdin=stdin)
        orgs = org_scope(org, scoped=bool(user or team or repos))
        filters = RepoSearchFilters(
            raw_query=query,
            user=user,
            orgs=orgs,
            repos=repos,
            name=name,
            language=language,
            archived=archived,
            fork=fork,
            visibility=visibility,
            sort=sort,
            limit=_probe(limit),
        )
        with open_client() as (client, ui):
            use_case = SearchRepos(
                client,
                client,
                warn=lambda text: ui.message("warning", text),
                note=lambda text: ui.message("info", text),
            )
            team_scopes = parse_team_scopes(team, orgs=orgs)
            with ui.progress("Searching repositories…"):
                rows = list(use_case(filters, team_scopes=team_scopes))
            rows = _cap(rows, limit, ui)
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="github.repo",
            table_columns=REPO_HIT_COLUMNS,
            empty="No repositories found. Broaden your query or remove scope filters.",
        )


@app.command(name="code")
def code_command(
    query: FreeTextArgument = None,
    /,
    *,
    user: UserOption = None,
    org: OrgOption = None,
    team: TeamOption = None,
    repo: RepoOption = None,
    stdin: StdinOption = False,
    language: LanguageOption = None,
    filename: Annotated[
        str | None, Parameter(name="--filename", help="Match the file name (filename:X).")
    ] = None,
    path: Annotated[
        str | None, Parameter(name="--path", help="Match the file path (path:X).")
    ] = None,
    extension: Annotated[
        str | None, Parameter(name="--extension", help="Match the file extension (extension:X).")
    ] = None,
    limit: SearchLimitOption = 30,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Search code (``GET /search/code``).

    Requires at least one scope qualifier on the GitHub side; this
    command injects ``user:@me`` if you pass none. GitHub no longer
    supports ``sort`` for code search — best-match is the only order.
    Use ``sweep`` for exhaustive regex, path, negation, or multi-ref
    queries; GitHub code search has no regex, caps at 1000 results, and
    searches the default branch only.
    """
    from untaped_github.application import SearchCode  # noqa: PLC0415
    from untaped_github.domain import CodeSearchFilters  # noqa: PLC0415

    with report_errors():
        repos = _repo_scopes(repo, stdin=stdin)
        orgs = org_scope(org, scoped=bool(user or team or repos))
        filters = CodeSearchFilters(
            raw_query=query,
            user=user,
            orgs=orgs,
            repos=repos,
            language=language,
            filename=filename,
            path=path,
            extension=extension,
            limit=_probe(limit),
        )
        with open_client() as (client, ui):
            use_case = SearchCode(
                client,
                client,
                warn=lambda text: ui.message("warning", text),
                note=lambda text: ui.message("info", text),
            )
            team_scopes = parse_team_scopes(team, orgs=orgs)
            with ui.progress("Searching code…"):
                rows = list(use_case(filters, team_scopes=team_scopes))
            rows = _cap(rows, limit, ui)
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="github.code",
            empty="No code matches found. Check syntax, language, and repository scope.",
        )


@app.command(name="issues")
def issues_command(
    query: FreeTextArgument = None,
    /,
    *,
    user: UserOption = None,
    org: OrgOption = None,
    team: TeamOption = None,
    repo: RepoOption = None,
    stdin: StdinOption = False,
    state: Annotated[
        Literal["open", "closed"] | None,
        Parameter(name="--state", help="Only open or only closed items."),
    ] = None,
    kind: Annotated[
        Literal["issue", "pr"] | None,
        Parameter(name="--kind", help="Only issues or only pull requests."),
    ] = None,
    author: Annotated[
        str | None, Parameter(name="--author", help="Created by this login (author:X).")
    ] = None,
    assignee: Annotated[
        str | None, Parameter(name="--assignee", help="Assigned to this login (assignee:X).")
    ] = None,
    label: Annotated[
        list[str] | None,
        Parameter(
            name="--label", help="Has this label. Repeatable.", consume_multiple=False, negative=""
        ),
    ] = None,
    mentions: Annotated[
        str | None, Parameter(name="--mentions", help="Mentions this login (mentions:X).")
    ] = None,
    sort: Annotated[
        Literal["comments", "reactions", "interactions", "created", "updated"] | None,
        Parameter(name="--sort", help="Sort order; best match when omitted."),
    ] = None,
    limit: SearchLimitOption = 30,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Search issues and pull requests (``GET /search/issues``)."""
    from untaped_github.application import SearchIssues  # noqa: PLC0415
    from untaped_github.domain import IssueSearchFilters  # noqa: PLC0415

    with report_errors():
        repos = _repo_scopes(repo, stdin=stdin)
        orgs = org_scope(org, scoped=bool(user or team or repos))
        filters = IssueSearchFilters(
            raw_query=query,
            user=user,
            orgs=orgs,
            repos=repos,
            state=state,
            kind=kind,
            author=author,
            assignee=assignee,
            labels=tuple(label or ()),
            mentions=mentions,
            sort=sort,
            limit=_probe(limit),
        )
        with open_client() as (client, ui):
            use_case = SearchIssues(
                client,
                client,
                warn=lambda text: ui.message("warning", text),
                note=lambda text: ui.message("info", text),
            )
            team_scopes = parse_team_scopes(team, orgs=orgs)
            with ui.progress("Searching issues and pull requests…"):
                rows = list(use_case(filters, team_scopes=team_scopes))
            rows = _cap(rows, limit, ui)
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="github.issue",
            empty="No issues or pull requests found. Expand your query or check "
            "state/label filters.",
        )


@app.command(name="users")
def users_command(
    query: FreeTextArgument = None,
    /,
    *,
    kind: Annotated[
        Literal["user", "org"] | None,
        Parameter(name="--kind", help="Only users or only organizations."),
    ] = None,
    location: Annotated[
        str | None, Parameter(name="--location", help="Match the profile location (location:X).")
    ] = None,
    language: LanguageOption = None,
    sort: Annotated[
        Literal["followers", "repositories", "joined"] | None,
        Parameter(name="--sort", help="Sort order; best match when omitted."),
    ] = None,
    limit: SearchLimitOption = 30,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Search users and organizations (``GET /search/users``)."""
    from untaped_github.application import SearchUsers  # noqa: PLC0415
    from untaped_github.domain import UserSearchFilters  # noqa: PLC0415

    with report_errors():
        filters = UserSearchFilters(
            raw_query=query,
            kind=kind,
            location=location,
            language=language,
            sort=sort,
            limit=_probe(limit),
        )
        with open_client() as (client, ui), ui.progress("Searching users…"):
            rows = list(SearchUsers(client)(filters))
        rows = _cap(rows, limit, ui)
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="github.user_hit",
            empty="No users or organizations found. Try different keywords or filters.",
        )
