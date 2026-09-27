"""Search query value objects for the GitHub bounded context.

Each filter object is a frozen pydantic model that knows how to render
itself into the ``q=`` value GitHub's search API expects. Use cases own
the orchestration (team-to-repos expansion, default-scope injection);
the query objects are pure — given the same inputs, they always emit
the same query string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from untaped.capabilities.github.domain.repo_filters import ArchivedMode

RepoSortOption = Literal["stars", "forks", "help-wanted-issues", "updated"]
IssueSortOption = Literal[
    "comments",
    "reactions",
    "interactions",
    "created",
    "updated",
]
UserSortOption = Literal["followers", "repositories", "joined"]


@dataclass(frozen=True)
class SearchQueryToken:
    """One whitespace-separated term of a raw search query."""

    value: str
    quoted: bool


def tokenize_search_query(raw_query: str) -> tuple[SearchQueryToken, ...]:
    """Split a raw query on whitespace outside double quotes, dropping the quotes."""
    tokens: list[SearchQueryToken] = []
    chars: list[str] = []
    quoted = False
    token_quoted = False

    for char in raw_query:
        if quoted:
            if char == '"':
                quoted = False
            else:
                chars.append(char)
            continue
        if char == '"':
            quoted = True
            token_quoted = True
            continue
        if char.isspace():
            if chars or token_quoted:
                tokens.append(SearchQueryToken("".join(chars), token_quoted))
                chars = []
                token_quoted = False
            continue
        chars.append(char)

    if chars or token_quoted:
        tokens.append(SearchQueryToken("".join(chars), token_quoted))
    return tuple(tokens)


def _has_qualifier(raw_query: str | None, key: str) -> bool:
    """Whether ``raw_query`` holds an unquoted ``key:`` (or negated ``-key:``) qualifier."""
    return any(
        not token.quoted and token.value.removeprefix("-").lower().startswith(f"{key}:")
        for token in tokenize_search_query(raw_query or "")
    )


def _quote(value: str) -> str:
    """Wrap a qualifier value in quotes if it contains whitespace."""
    return f'"{value}"' if any(c.isspace() for c in value) else value


def _repo_scope(repos: tuple[str, ...]) -> str | None:
    """Render repository scopes with OR semantics when multiple are present."""
    if not repos:
        return None
    if len(repos) == 1:
        return f"repo:{repos[0]}"
    return "(" + " OR ".join(f"repo:{repo}" for repo in repos) + ")"


class _QueryBase(BaseModel):
    """Fields shared by every search type."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_query: str | None = None
    # ``None`` means unbounded (paginate until exhausted or GitHub's
    # 1000-result cap). The CLI always supplies an int; non-CLI
    # callers (tests, programmatic use) may still pass ``None``.
    limit: int | None = None

    def _prefix(self) -> list[str]:
        return [self.raw_query.strip()] if self.raw_query else []

    def to_query_string(self) -> str:
        raise NotImplementedError


class ScopedQueryBase(_QueryBase):
    """Adds the scope qualifiers used by repos/code/issues search."""

    user: str | None = None
    orgs: tuple[str, ...] = ()
    repos: tuple[str, ...] = ()

    def _scope_qualifiers(self) -> list[str]:
        parts: list[str] = []
        if self.user:
            parts.append(f"user:{self.user}")
        parts.extend(f"org:{org}" for org in self.orgs)
        repo_scope = _repo_scope(self.repos)
        if repo_scope:
            parts.append(repo_scope)
        return parts

    def _assemble(self, *extra_qualifiers: str) -> str:
        parts = [*self._prefix(), *self._scope_qualifiers()]
        parts.extend(q for q in extra_qualifiers if q)
        return " ".join(p for p in parts if p)


class RepoSearchFilters(ScopedQueryBase):
    """Filters for ``GET /search/repositories``."""

    name: str | None = None
    language: str | None = None
    # Neutral (no qualifier) by default; the CLI applies the user-facing "exclude".
    archived: ArchivedMode = "include"
    fork: bool | None = None
    visibility: Literal["public", "private"] | None = None
    sort: RepoSortOption | None = None

    def to_query_string(self) -> str:
        extras: list[str] = []
        if self.name:
            extras.append(f"{_quote(self.name)} in:name")
        if self.language:
            extras.append(f"language:{_quote(self.language)}")
        if self.archived != "include" and not _has_qualifier(self.raw_query, "archived"):
            extras.append(f"archived:{'true' if self.archived == 'only' else 'false'}")
        if self.fork is not None:
            extras.append("fork:true" if self.fork else "fork:false")
        if self.visibility is not None:
            extras.append(f"is:{self.visibility}")
        return self._assemble(*extras)


class CodeSearchFilters(ScopedQueryBase):
    """Filters for ``GET /search/code``."""

    language: str | None = None
    filename: str | None = None
    path: str | None = None
    extension: str | None = None

    def to_query_string(self) -> str:
        extras: list[str] = []
        if self.language:
            extras.append(f"language:{_quote(self.language)}")
        if self.filename:
            extras.append(f"filename:{_quote(self.filename)}")
        if self.path:
            extras.append(f"path:{_quote(self.path)}")
        if self.extension:
            extras.append(f"extension:{_quote(self.extension)}")
        return self._assemble(*extras)


class IssueSearchFilters(ScopedQueryBase):
    """Filters for ``GET /search/issues``."""

    state: Literal["open", "closed"] | None = None
    kind: Literal["issue", "pr"] | None = None
    author: str | None = None
    assignee: str | None = None
    labels: tuple[str, ...] = ()
    mentions: str | None = None
    sort: IssueSortOption | None = None

    def to_query_string(self) -> str:
        extras: list[str] = []
        if self.kind is not None:
            extras.append(f"is:{self.kind}")
        if self.state is not None:
            extras.append(f"is:{self.state}")
        if self.author:
            extras.append(f"author:{self.author}")
        if self.assignee:
            extras.append(f"assignee:{self.assignee}")
        if self.mentions:
            extras.append(f"mentions:{self.mentions}")
        extras.extend(f"label:{_quote(label)}" for label in self.labels)
        return self._assemble(*extras)


class UserSearchFilters(_QueryBase):
    """Filters for ``GET /search/users``.

    The user-search endpoint ignores ``user:`` / ``repo:`` / ``org:``
    qualifiers, so this filter intentionally does not inherit the
    scope mixin. ``extra="forbid"`` makes a typo like
    ``UserSearchFilters(user="@me")`` fail loudly instead of silently
    dropping the value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["user", "org"] | None = None
    location: str | None = None
    language: str | None = None
    sort: UserSortOption | None = None

    def to_query_string(self) -> str:
        parts = self._prefix()
        if self.kind is not None:
            parts.append(f"type:{self.kind}")
        if self.location:
            parts.append(f"location:{_quote(self.location)}")
        if self.language:
            parts.append(f"language:{_quote(self.language)}")
        return " ".join(p for p in parts if p)
