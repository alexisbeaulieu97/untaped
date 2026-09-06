"""Domain entities for the GitHub bounded context."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RefKind = Literal["heads", "tags"]
"""Ref namespace probed by ``GithubClient.batch_repo_refs``."""

BatchRepoRefsFailureKind = Literal["server_error", "transport"]
"""Retryable per-repo failure class from a batched GraphQL ref probe."""


class GithubUser(BaseModel):
    """Authenticated GitHub user as returned by ``GET /user``."""

    model_config = ConfigDict(extra="ignore")

    login: str
    id: int
    name: str | None = None
    email: str | None = None


class RepoResult(BaseModel):
    """One row of ``GET /search/repositories``."""

    model_config = ConfigDict(extra="ignore")

    full_name: str
    id: int
    name: str
    html_url: str
    description: str | None = None
    language: str | None = None
    stargazers_count: int = 0
    forks_count: int = 0
    archived: bool = False
    fork: bool = False
    private: bool = False
    updated_at: str | None = None


class RepoListResult(BaseModel):
    """One row from GitHub repository inventory list endpoints."""

    model_config = ConfigDict(extra="ignore")

    full_name: str
    name: str
    html_url: str | None = None
    clone_url: str | None = None
    ssh_url: str | None = None
    default_branch: str | None = None
    private: bool = False
    archived: bool = False
    fork: bool = False


class CodeResult(BaseModel):
    """One row of ``GET /search/code``.

    Flattens ``repository.full_name`` into ``repo`` so column selection
    stays one level deep for the common case; the full nested dict
    remains accessible via ``repository``.
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    path: str
    sha: str
    html_url: str
    repo: str = ""
    repository: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _flatten_repository(cls, data: Any) -> Any:
        if isinstance(data, dict) and "repo" not in data:
            repository = data.get("repository") or {}
            if isinstance(repository, dict):
                return {**data, "repo": repository.get("full_name", "")}
        return data


class CorpusRepoResult(BaseModel):
    """One repository row in the local scan corpus."""

    model_config = ConfigDict(extra="ignore")

    repo: str
    ref: str
    path: str
    clone_url: str | None = None
    status: Literal["synced", "cached", "removed"] = "cached"
    fetched_at: str | None = None
    profile: str = "default"
    ref_globs: tuple[str, ...] = ()
    archived: bool = False
    disk_bytes: int = 0


class CodeHitResult(BaseModel):
    """One line matched by the local Git corpus scanner."""

    model_config = ConfigDict(extra="ignore")

    repo: str
    ref: str
    path: str
    line: int
    column: int
    text: str


class WorktreeResult(BaseModel):
    """A materialized worktree path for a cached repository ref."""

    model_config = ConfigDict(extra="ignore")

    repo: str
    ref: str
    path: str


class IssueResult(BaseModel):
    """One row of ``GET /search/issues``.

    Covers both issues and pull requests — distinguished by the presence
    of ``pull_request`` in the raw payload, surfaced here as
    ``is_pull_request``.
    """

    model_config = ConfigDict(extra="ignore")

    repo: str = ""
    number: int
    id: int
    title: str
    state: str
    html_url: str
    repository_url: str
    user_login: str | None = None
    is_pull_request: bool = False

    @model_validator(mode="before")
    @classmethod
    def _flatten_issue(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        patch: dict[str, Any] = {}
        if "repo" not in data:
            repo = _repo_from_repository_url(data.get("repository_url"))
            if repo:
                patch["repo"] = repo
        if "user_login" not in data:
            user = data.get("user") or {}
            if isinstance(user, dict):
                patch["user_login"] = user.get("login")
        if "is_pull_request" not in data:
            patch["is_pull_request"] = "pull_request" in data and data["pull_request"] is not None
        return {**data, **patch} if patch else data


def _repo_from_repository_url(value: Any) -> str | None:
    """Extract ``owner/name`` from GitHub's repository API URL."""
    if not isinstance(value, str):
        return None
    marker = "/repos/"
    if marker not in value:
        return None
    return value.rsplit(marker, 1)[1].strip("/") or None


class UserResult(BaseModel):
    """One row of ``GET /search/users``."""

    model_config = ConfigDict(extra="ignore")

    id: int
    login: str
    type: str
    html_url: str


class RepoRef(BaseModel):
    """One branch or tag head from the GraphQL batched ref probe.

    ``sha`` is the peeled oid: annotated tags are peeled up to two
    levels (covering tags-of-tags), so deeper tag chains return the
    innermost fetched oid rather than the final commit.
    """

    model_config = ConfigDict(frozen=True)

    kind: RefKind
    name: str
    sha: str


class RepoRefs(BaseModel):
    """All probed refs for one repository, in query ``kinds`` order."""

    model_config = ConfigDict(frozen=True)

    full_name: str
    default_branch: str | None = None
    refs: tuple[RepoRef, ...] = ()


class BatchRepoRefsFailure(BaseModel):
    """Retryable per-repo failure from a batched GraphQL ref probe."""

    model_config = ConfigDict(frozen=True)

    full_name: str
    reason: str
    kind: BatchRepoRefsFailureKind
    status_code: int | None = None
    url: str | None = None


class BatchRepoRefsResult(BaseModel):
    """Outcome of a batched GraphQL ref probe.

    ``repos`` preserves input order, skipping entries listed in
    ``missing`` (repositories GitHub reported as ``NOT_FOUND`` or
    ``FORBIDDEN``) or ``failures`` (repositories whose chunk was narrowed
    down to a retryable transient failure). ``rate_limit_cost`` is the
    summed GraphQL ``rateLimit.cost`` across all POSTs in this operation, while
    ``rate_limit_remaining`` and ``rate_limit_reset_at`` surface the
    latest available budget values so callers can warn or stop when the
    hourly budget runs low.
    """

    model_config = ConfigDict(frozen=True)

    repos: tuple[RepoRefs, ...] = ()
    missing: tuple[str, ...] = ()
    failures: tuple[BatchRepoRefsFailure, ...] = ()
    rate_limit_cost: int | None = None
    rate_limit_remaining: int | None = None
    rate_limit_reset_at: datetime | None = None
