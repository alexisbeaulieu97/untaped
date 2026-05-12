"""Search query value objects for the GitHub bounded context.

Each filter object is a frozen pydantic model that knows how to render
itself into the ``q=`` value GitHub's search API expects. Use cases own
the orchestration (team-to-repos expansion, default-scope injection);
the query objects are pure — given the same inputs, they always emit
the same query string.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


def _quote(value: str) -> str:
    """Wrap a qualifier value in quotes if it contains whitespace."""
    return f'"{value}"' if any(c.isspace() for c in value) else value


class _BaseSearchFilters(BaseModel):
    """Fields shared by every search type."""

    model_config = ConfigDict(frozen=True)

    raw_query: str | None = None
    user: str | None = None
    orgs: tuple[str, ...] = ()
    repos: tuple[str, ...] = ()
    sort: str | None = None
    limit: int | None = None

    def _scope_qualifiers(self) -> list[str]:
        parts: list[str] = []
        if self.user:
            parts.append(f"user:{self.user}")
        parts.extend(f"org:{org}" for org in self.orgs)
        parts.extend(f"repo:{repo}" for repo in self.repos)
        return parts

    def _assemble(self, *extra_qualifiers: str) -> str:
        parts: list[str] = []
        if self.raw_query:
            parts.append(self.raw_query.strip())
        parts.extend(self._scope_qualifiers())
        parts.extend(q for q in extra_qualifiers if q)
        return " ".join(p for p in parts if p)


class RepoSearchFilters(_BaseSearchFilters):
    """Filters for ``GET /search/repositories``."""

    name: str | None = None
    language: str | None = None
    archived: bool | None = None
    fork: bool | None = None
    visibility: Literal["public", "private"] | None = None

    def to_query_string(self) -> str:
        extras: list[str] = []
        if self.name:
            extras.append(f"{_quote(self.name)} in:name")
        if self.language:
            extras.append(f"language:{_quote(self.language)}")
        if self.archived is not None:
            extras.append(f"archived:{'true' if self.archived else 'false'}")
        if self.fork is not None:
            extras.append("fork:true" if self.fork else "fork:false")
        if self.visibility is not None:
            extras.append(f"is:{self.visibility}")
        return self._assemble(*extras)


class CodeSearchFilters(_BaseSearchFilters):
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


class IssueSearchFilters(_BaseSearchFilters):
    """Filters for ``GET /search/issues``."""

    state: Literal["open", "closed"] | None = None
    kind: Literal["issue", "pr"] | None = None
    author: str | None = None
    assignee: str | None = None
    labels: tuple[str, ...] = ()
    mentions: str | None = None

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


class UserSearchFilters(_BaseSearchFilters):
    """Filters for ``GET /search/users``.

    Note: the user/org search endpoint does not accept ``user:`` or
    ``repo:`` qualifiers — only ``type:``, ``in:``, ``location:``,
    ``language:``, and a few others. The ``user/orgs/repos`` fields on
    the base are kept for API symmetry but ignored here (they would
    return zero results upstream).
    """

    kind: Literal["user", "org"] | None = None
    location: str | None = None
    language: str | None = None

    def to_query_string(self) -> str:
        parts: list[str] = []
        if self.raw_query:
            parts.append(self.raw_query.strip())
        if self.kind is not None:
            parts.append(f"type:{self.kind}")
        if self.location:
            parts.append(f"location:{_quote(self.location)}")
        if self.language:
            parts.append(f"language:{_quote(self.language)}")
        return " ".join(p for p in parts if p)
