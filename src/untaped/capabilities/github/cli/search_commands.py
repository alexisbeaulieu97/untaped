"""Cyclopts sub-app: ``untaped github search``.

Four subcommands, one per GitHub search endpoint. Each builds a frozen
filter object from CLI flags, hands it to its use case, and pipes the
result through core's ``emit`` helper. Composition lives here;
the use cases own the orchestration.
"""

from __future__ import annotations

from typing import Annotated, Literal

from cyclopts import Parameter, validators

from untaped.capabilities.github.cli._client import open_client
from untaped.capabilities.github.cli.scopes import (
    REPO_KINDS,
    OrgOption,
    TeamOption,
    parse_team_scopes,
)
from untaped.capability_api import (
    ColumnsOption,
    FormatOption,
    StdinOption,
    create_app,
    deprecated_alias,
    emit,
    read_identifiers,
    report_errors,
)

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
    Parameter(name="--user", help="user:LOGIN. Defaults to @me when no other scope is set."),
]
RepoOption = Annotated[
    list[str] | None,
    Parameter(
        name="--repo", help="repo:OWNER/NAME. Repeatable.", consume_multiple=False, negative=""
    ),
]
LanguageOption = Annotated[
    str | None, Parameter(name="--language", help="Match the language (language:X).")
]

app = create_app(
    name="search",
    help="Search GitHub for repos, code, issues, and users.",
)


def _repo_scopes(values: list[str] | None, *, stdin: bool) -> tuple[str, ...]:
    """Merge explicit ``--repo`` values with optional stdin repo scopes."""
    repos = list(values or ())
    if stdin:
        repos.extend(
            read_identifiers([], stdin=True, id_field="full_name", accept_kinds=REPO_KINDS)
        )
    return tuple(repos)


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
    archived: Annotated[
        bool | None,
        Parameter(
            name="--archived",
            negative="--no-archived",
            help="Only archived repos; --no-archived excludes them.",
        ),
    ] = None,
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
    from untaped.capabilities.github.application import SearchRepos  # noqa: PLC0415
    from untaped.capabilities.github.domain import RepoSearchFilters  # noqa: PLC0415

    with report_errors():
        orgs = tuple(org or ())
        filters = RepoSearchFilters(
            raw_query=query,
            user=user,
            orgs=orgs,
            repos=_repo_scopes(repo, stdin=stdin),
            name=name,
            language=language,
            archived=archived,
            fork=fork,
            visibility=visibility,
            sort=sort,
            limit=limit,
        )
        with open_client() as (client, ui):
            use_case = SearchRepos(client, client, warn=lambda text: ui.message("warning", text))
            team_scopes = parse_team_scopes(team, orgs=orgs)
            with ui.progress("Searching repositories…"):
                rows = [r.model_dump() for r in use_case(filters, team_scopes=team_scopes)]
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="github.repo_hit",
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
    from untaped.capabilities.github.application import SearchCode  # noqa: PLC0415
    from untaped.capabilities.github.domain import CodeSearchFilters  # noqa: PLC0415

    with report_errors():
        orgs = tuple(org or ())
        filters = CodeSearchFilters(
            raw_query=query,
            user=user,
            orgs=orgs,
            repos=_repo_scopes(repo, stdin=stdin),
            language=language,
            filename=filename,
            path=path,
            extension=extension,
            limit=limit,
        )
        with open_client() as (client, ui):
            use_case = SearchCode(client, client, warn=lambda text: ui.message("warning", text))
            team_scopes = parse_team_scopes(team, orgs=orgs)
            with ui.progress("Searching code…"):
                rows = [r.model_dump() for r in use_case(filters, team_scopes=team_scopes)]
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
    from untaped.capabilities.github.application import SearchIssues  # noqa: PLC0415
    from untaped.capabilities.github.domain import IssueSearchFilters  # noqa: PLC0415

    with report_errors():
        orgs = tuple(org or ())
        filters = IssueSearchFilters(
            raw_query=query,
            user=user,
            orgs=orgs,
            repos=_repo_scopes(repo, stdin=stdin),
            state=state,
            kind=kind,
            author=author,
            assignee=assignee,
            labels=tuple(label or ()),
            mentions=mentions,
            sort=sort,
            limit=limit,
        )
        with open_client() as (client, ui):
            use_case = SearchIssues(client, client, warn=lambda text: ui.message("warning", text))
            team_scopes = parse_team_scopes(team, orgs=orgs)
            with ui.progress("Searching issues and pull requests…"):
                rows = [r.model_dump() for r in use_case(filters, team_scopes=team_scopes)]
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
    from untaped.capabilities.github.application import SearchUsers  # noqa: PLC0415
    from untaped.capabilities.github.domain import UserSearchFilters  # noqa: PLC0415

    with report_errors():
        filters = UserSearchFilters(
            raw_query=query,
            kind=kind,
            location=location,
            language=language,
            sort=sort,
            limit=limit,
        )
        with open_client() as (client, ui), ui.progress("Searching users…"):
            rows = [r.model_dump() for r in SearchUsers(client)(filters)]
        emit(
            rows,
            fmt=fmt,
            columns=columns,
            kind="github.user_hit",
            empty="No users or organizations found. Try different keywords or filters.",
        )


for _command in ("repos", "code", "issues"):
    deprecated_alias(app[_command], "--repo-stdin", "--stdin")
