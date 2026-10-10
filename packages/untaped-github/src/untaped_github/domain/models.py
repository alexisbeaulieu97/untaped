"""Domain entities for the GitHub bounded context."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from untaped.sdk import AbsolutePath, OutcomeRecord, Record, TargetRecord, UtcTimestamp

RefKind = Literal["heads", "tags"]
"""Ref namespace probed by ``GithubClient.batch_repo_refs``."""

BatchRepoRefsFailureKind = Literal["server_error", "transport"]
"""Retryable per-repo failure class from a batched GraphQL ref probe."""


def _with_aliases(data: Any) -> Any:
    """Derive ``url`` from ``html_url``.

    ``url`` always mirrors ``html_url``: the raw GitHub payload's own ``url``
    is the API link, which must not leak into the web-URL field. A record
    without ``html_url`` (one this model already emitted) keeps its ``url``.
    """
    if not isinstance(data, dict) or "html_url" not in data:
        return data
    return {**data, "url": data["html_url"]}


class GithubUser(BaseModel):
    """Authenticated GitHub user as returned by ``GET /user``."""

    model_config = ConfigDict(extra="ignore")

    login: str
    id: int
    name: str | None = None
    email: str | None = None


class GithubRepo(Record, kind="github.repo"):
    """A GitHub repository, as every github command emits and reads it (``github.repo``).

    The fields are GitHub's own (``full_name``, ``html_url``, ``clone_url``…),
    so a raw REST row validates as it is and unknown fields are dropped; only
    ``full_name`` is required, and a field the row's source didn't report is
    ``None``: a search hit lacks ``clone_url`` and ``pushed_at``, a cache row
    most of GitHub's metadata. github fills
    workspace's ``RepoSource`` with it, so a piped row becomes a workspace repo
    without an API call.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)
    table_columns: ClassVar[tuple[str, ...]] = (
        "full_name",
        "default_branch",
        "private",
        "pushed_at",
        "description",
    )

    full_name: str
    name: str | None = Field(default=None, validate_default=True)
    html_url: str | None = None
    clone_url: str | None = None
    ssh_url: str | None = None
    default_branch: str | None = None
    description: str | None = None
    language: str | None = None
    stargazers_count: int | None = None
    forks_count: int | None = None
    private: bool | None = None
    archived: bool = False
    fork: bool | None = None
    # GitHub's last push, in GitHub's own ``…Z`` form: piped into sweep or
    # cache sync it matches the stored value and skips an unchanged repo.
    pushed_at: str | None = None
    updated_at: str | None = None

    @field_validator("name")
    @classmethod
    def _short_name(cls, name: str | None, info: ValidationInfo) -> str | None:
        # An "after" field validator, not a "before" model one: that would
        # hand the rest of a JSON row to strict validation as Python values.
        full_name = info.data.get("full_name")
        if not name and isinstance(full_name, str):
            return full_name.rpartition("/")[2]
        return name


#: The columns ``search repos`` shows by default.
REPO_HIT_COLUMNS = ("full_name", "description", "language", "stargazers_count", "updated_at")


class CodeResult(BaseModel):
    """One row of ``GET /search/code``.

    Flattens ``repository.full_name`` into ``repo`` so column selection
    stays one level deep.
    """

    model_config = ConfigDict(extra="ignore")
    table_columns: ClassVar[tuple[str, ...]] = ("repo", "path")

    name: str
    path: str
    sha: str
    repo: str = ""
    url: str

    @model_validator(mode="before")
    @classmethod
    def _url(cls, data: Any) -> Any:
        return _with_aliases(data)

    @model_validator(mode="before")
    @classmethod
    def _flatten_repository(cls, data: Any) -> Any:
        if isinstance(data, dict) and "repo" not in data:
            repository = data.get("repository") or {}
            if isinstance(repository, dict):
                return {**data, "repo": repository.get("full_name", "")}
        return data


class CorpusRepoResult(GithubRepo, kind="github.corpus_repo"):
    """One repository row in the local scan corpus (``github.corpus_repo``).

    ``path`` is the repo in the git plugin's repo store. ``cache delete`` and
    ``prune`` rows say ``removed`` (nobody else used the repo; ``disk_bytes``
    is what it freed) or ``released`` (github's refs, metadata and worktrees
    went; ``kept`` names who still holds the repo, and ``disk_bytes`` is 0).
    A :class:`GithubRepo`, so it pipes into workspace like any repo row.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("full_name", "ref", "status", "fetched_at", "path")

    full_name: str
    ref: str
    path: str
    status: Literal["synced", "cached", "released", "removed"] = "cached"
    kept: str | None = None
    fetched_at: str | None = None
    profile: str = "default"
    ref_globs: tuple[str, ...] = ()
    disk_bytes: int = 0


class CorpusSyncOutcome(OutcomeRecord):
    """The result of warming one repository in the corpus (``github.sync_outcome``).

    ``action`` is ``synced`` (fetched), ``unchanged`` (GitHub reports no push
    since the cached fetch, so none ran), ``skipped`` (the cached copy is
    younger than ``github.sweep.max_age_seconds``), or ``failed``. A failed
    row says why in ``detail`` and carries the structured ``error``.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("repo", "action", "detail")

    repo: str
    fetched_at: UtcTimestamp | None = None
    detail: str | None = None


class WorktreeResult(TargetRecord):
    """A materialized worktree path for a cached repository ref (``github.worktree``).

    ``target_path`` leads so ``--format raw`` prints it, for ``$(…)`` capture,
    and so ``--format pipe`` consumers find the directory. ``path`` holds the
    same directory; it predates ``target_path`` and stays for scripts that read it.
    """

    target_path: AbsolutePath
    path: str
    repo: str
    ref: str


class IssueResult(BaseModel):
    """One row of ``GET /search/issues``.

    Covers both issues and pull requests — distinguished by the presence
    of ``pull_request`` in the raw payload, surfaced here as
    ``is_pull_request``.
    """

    model_config = ConfigDict(extra="ignore")
    table_columns: ClassVar[tuple[str, ...]] = ("repo", "number", "title", "state", "user_login")

    repo: str = ""
    number: int
    id: int
    title: str
    state: str
    url: str
    user_login: str | None = None
    is_pull_request: bool = False

    @model_validator(mode="before")
    @classmethod
    def _url(cls, data: Any) -> Any:
        return _with_aliases(data)

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
    table_columns: ClassVar[tuple[str, ...]] = ("login", "type")

    id: int
    login: str
    type: str
    url: str

    @model_validator(mode="before")
    @classmethod
    def _url(cls, data: Any) -> Any:
        return _with_aliases(data)


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
