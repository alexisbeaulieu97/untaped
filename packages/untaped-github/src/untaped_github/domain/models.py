"""Domain entities for the GitHub bounded context."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

RefKind = Literal["heads", "tags"]
"""Ref namespace probed by ``GithubClient.batch_repo_refs``."""

BatchRepoRefsFailureKind = Literal["server_error", "transport"]
"""Retryable per-repo failure class from a batched GraphQL ref probe."""


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
