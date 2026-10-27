"""``untaped github search``: one command per GitHub search endpoint.

Each builds a frozen filter from its options and hands it to its use case;
the use cases own the orchestration.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import Field

from untaped.sdk import Group, Limit, Output, Positional, Short, Stdin, plural
from untaped_github.application import SearchCode, SearchIssues, SearchRepos, SearchUsers
from untaped_github.commands._github import (
    ARCHIVED,
    FORK,
    FORKS,
    REPO,
    ForkMode,
    GithubCommand,
    Scope,
    team_scopes,
)
from untaped_github.domain import (
    ArchivedMode,
    CodeSearchFilters,
    IssueSearchFilters,
    RepoSearchFilters,
    UserSearchFilters,
)
from untaped_github.records import REPO_HIT_COLUMNS, CorpusRepo, Issue, Repo, SweepRepo, UserHit
from untaped_github.records import Code as CodeHit

#: GitHub serves at most this many results per search, so no row past it can
#: reveal that a --limit truncated the output.
_GITHUB_SEARCH_CAP = 1000
#: Search pages hold at most this many rows; a probe row past a full page would
#: cost a whole extra request against the per-minute search budget.
_GITHUB_SEARCH_PAGE = 100

QUERY = "Free-text query (passed verbatim)."
LANGUAGE = "Match the language (language:X)."


class _Search(GithubCommand):
    """A search over GitHub's index, up to GitHub's 1000 results."""

    query: Positional[str | None] = Field(None, description=QUERY)
    limit: Limit = Limit(default=30)

    def probe(self) -> int:
        """Ask for one row past the limit, so :meth:`cap` can tell whether more match.

        Skipped where it would need an extra request: at GitHub's cap and on a page boundary.
        """
        limit = self._limit()
        if limit >= _GITHUB_SEARCH_CAP or limit % _GITHUB_SEARCH_PAGE == 0:
            return limit
        return limit + 1

    def cap[T](self, rows: list[T]) -> list[T]:
        """Keep the limit's rows; note on stderr when the probe row shows more match."""
        limit = self._limit()
        if len(rows) > limit:
            self.ctx.diag.info(
                f"showing the first {plural(limit, 'result')}; "
                "more match, raise --limit to see them",
            )
        return rows[:limit]

    def _limit(self) -> int:
        return self.limit.value or _GITHUB_SEARCH_CAP


class _ScopedSearch(_Search):
    """A search scoped to users, orgs, teams and repos (``Scope`` explains them)."""

    user: str | None = Field(
        None, description="user:LOGIN. Without any scope: github.default_org, else @me."
    )
    scope: Scope
    repo: Annotated[list[str], Short("-r")] = Field(default_factory=list, description=REPO)
    stdin: Stdin[Repo | SweepRepo | CorpusRepo]

    def repos(self) -> tuple[str, ...]:
        """``--repo`` values, then the repos ``--stdin`` names."""
        if not self.stdin.requested:
            return tuple(self.repo)
        return (*self.repo, *self.stdin.identifiers(allow_empty=True))

    def scoped_orgs(self, repos: tuple[str, ...]) -> tuple[str, ...]:
        return self.orgs(self.scope, scoped=bool(self.user or self.scope.team or repos))


class Repos(_ScopedSearch):
    """Search repositories (``GET /search/repositories``).

    A ranked answer; ``repos list`` gives every repo of a scope. Sorted by
    ``stars``, ``forks`` or ``updated``, it queries every batch of a large
    scope and merge-sorts the rows before the limit; best match, and
    ``help-wanted-issues`` (which warns), stop once ``--limit`` unique rows
    arrive, so their order holds only within a batch. An unquoted
    ``archived:`` qualifier in the query wins over ``--archived``.
    GitHub serves at most 1000 results; ``--limit 1000`` asks for them all.
    """

    name: str | None = Field(None, description="Match against repo name (in:name).")
    language: str | None = Field(None, description=LANGUAGE)
    archived: ArchivedMode = Field("exclude", description=ARCHIVED)
    fork: ForkMode = Field("include", description=FORK)
    visibility: Literal["public", "private"] | None = Field(
        None, description="Only public or only private repos."
    )
    sort: Literal["stars", "forks", "help-wanted-issues", "updated"] | None = Field(
        None, description="Sort order; best match when omitted."
    )
    output: Output[Repo] = Output(columns=REPO_HIT_COLUMNS)

    def run(self) -> Iterator[Repo]:
        repos = self.repos()
        orgs = self.scoped_orgs(repos)
        filters = RepoSearchFilters(
            raw_query=self.query,
            user=self.user,
            orgs=orgs,
            repos=repos,
            name=self.name,
            language=self.language,
            archived=self.archived,
            fork=FORKS[self.fork],
            visibility=self.visibility,
            sort=self.sort,
            limit=self.probe(),
        )
        self.output.empty("No repositories found. Broaden your query or remove scope filters.")
        with self.client() as client:
            diag = self.ctx.diag
            search = SearchRepos(client, client, warn=diag.warning, note=diag.info)
            teams = team_scopes(self.scope.team, orgs=orgs)
            with diag.progress("Searching repositories"):
                rows = list(search(filters, team_scopes=teams))
        yield from self.cap(rows)


class Code(_ScopedSearch):
    """Search code (``GET /search/code``).

    Requires at least one scope qualifier on the GitHub side; this command
    injects ``user:@me`` if you pass none. GitHub no longer supports ``sort``
    for code search: best match is the only order. Use ``sweep`` for
    exhaustive regex, path, negation, or multi-ref queries; GitHub code search
    has no regex, caps at 1000 results, and searches the default branch only.
    """

    language: str | None = Field(None, description=LANGUAGE)
    filename: str | None = Field(None, description="Match the file name (filename:X).")
    path: str | None = Field(None, description="Match the file path (path:X).")
    extension: str | None = Field(None, description="Match the file extension (extension:X).")
    output: Output[CodeHit]

    def run(self) -> Iterator[CodeHit]:
        repos = self.repos()
        orgs = self.scoped_orgs(repos)
        filters = CodeSearchFilters(
            raw_query=self.query,
            user=self.user,
            orgs=orgs,
            repos=repos,
            language=self.language,
            filename=self.filename,
            path=self.path,
            extension=self.extension,
            limit=self.probe(),
        )
        self.output.empty("No code matches found. Check syntax, language, and repository scope.")
        with self.client() as client:
            diag = self.ctx.diag
            search = SearchCode(client, client, warn=diag.warning, note=diag.info)
            teams = team_scopes(self.scope.team, orgs=orgs)
            with diag.progress("Searching code"):
                rows = list(search(filters, team_scopes=teams))
        yield from self.cap(rows)


class Issues(_ScopedSearch):
    """Search issues and pull requests (``GET /search/issues``).

    A sorted search (``--sort``, or a ``sort:FIELD[-asc|-desc]`` qualifier in
    the query) queries every batch of a large scope and merge-sorts the rows
    before the limit; an unsupported ``sort:`` field warns and keeps batch
    order. GitHub serves at most 1000 results; ``--limit 1000`` asks for them all.

    Examples
    --------
    ```console
    $ untaped github search issues --org acme --state open --kind pr
    ```
    """

    state: Literal["open", "closed"] | None = Field(
        None, description="Only open or only closed items."
    )
    kind: Literal["issue", "pr"] | None = Field(
        None, description="Only issues or only pull requests."
    )
    author: str | None = Field(None, description="Created by this login (author:X).")
    assignee: str | None = Field(None, description="Assigned to this login (assignee:X).")
    label: list[str] = Field(default_factory=list, description="Has this label. Repeatable.")
    mentions: str | None = Field(None, description="Mentions this login (mentions:X).")
    sort: Literal["comments", "reactions", "interactions", "created", "updated"] | None = Field(
        None, description="Sort order; best match when omitted."
    )
    output: Output[Issue]

    def run(self) -> Iterator[Issue]:
        repos = self.repos()
        orgs = self.scoped_orgs(repos)
        filters = IssueSearchFilters(
            raw_query=self.query,
            user=self.user,
            orgs=orgs,
            repos=repos,
            state=self.state,
            kind=self.kind,
            author=self.author,
            assignee=self.assignee,
            labels=tuple(self.label),
            mentions=self.mentions,
            sort=self.sort,
            limit=self.probe(),
        )
        self.output.empty(
            "No issues or pull requests found. Expand your query or check state/label filters."
        )
        with self.client() as client:
            diag = self.ctx.diag
            search = SearchIssues(client, client, warn=diag.warning, note=diag.info)
            teams = team_scopes(self.scope.team, orgs=orgs)
            with diag.progress("Searching issues and pull requests"):
                rows = list(search(filters, team_scopes=teams))
        yield from self.cap(rows)


class Users(_Search):
    """Search users and organizations (``GET /search/users``).

    GitHub serves at most 1000 results; ``--limit 1000`` asks for them all.
    """

    kind: Literal["user", "org"] | None = Field(
        None, description="Only users or only organizations."
    )
    location: str | None = Field(None, description="Match the profile location (location:X).")
    language: str | None = Field(None, description=LANGUAGE)
    sort: Literal["followers", "repositories", "joined"] | None = Field(
        None, description="Sort order; best match when omitted."
    )
    output: Output[UserHit]

    def run(self) -> Iterator[UserHit]:
        filters = UserSearchFilters(
            raw_query=self.query,
            kind=self.kind,
            location=self.location,
            language=self.language,
            sort=self.sort,
            limit=self.probe(),
        )
        self.output.empty("No users or organizations found. Try different keywords or filters.")
        with self.client() as client, self.ctx.diag.progress("Searching users"):
            rows = list(SearchUsers(client)(filters))
        yield from self.cap(rows)


class SearchGroup(Group):
    """Search GitHub for repos, code, issues, and users."""

    commands = (Repos, Code, Issues, Users)
