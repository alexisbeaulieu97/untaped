"""GraphQL-backed remote ref freshness probe satisfying the RefProbe port.

All-ref scans use ``batch_repo_refs``; default-branch scans with no explicit
ref filters use ``batch_default_branch_refs``. Annotated tags are peeled up
to two levels, matching the SHA the Git probe records for branches,
lightweight tags, annotated tags, and tags-of-tags; 3+ level tag chains can
churn if a refresh switches backend.
Known limitation: an all-repo per-alias ``FORBIDDEN`` inside a ``200 OK``
response is still reported as per-repo missing/inaccessible.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Literal

from untaped.sdk import ErrorCategory
from untaped_ansible.domain.payloads import (
    GitRef,
    ProbedRepo,
    ProbeFailure,
    ProbeReport,
    ProbeTarget,
)

if TYPE_CHECKING:
    from untaped_ansible.application.ports import BatchRepoRefsClient
    from untaped_github.api import BatchRepoRefsFailure

_MISSING_REASON = "repository not found or inaccessible on GitHub"
TRANSIENT_REF_PROBE_FAILURE_PREFIX = "transient ref probe failed: "


class GithubRefProbe:
    """Probe branch/tag heads for many repos through github's batched GraphQL probe.

    github chunks the repos and runs ``concurrency`` chunks at once; this
    adapter turns what it read into the probe report. A repo github could
    not read becomes a failure; a global GraphQL failure aborts the probe.
    """

    def __init__(self, github: BatchRepoRefsClient, *, concurrency: int = 8) -> None:
        if concurrency < 1 or concurrency > 32:
            raise ValueError("concurrency must be between 1 and 32")
        self._github = github
        self._concurrency = concurrency

    def probe(
        self,
        repos: Sequence[ProbeTarget],
        *,
        kinds: Sequence[str],
        mode: Literal["all", "default_branch"] = "all",
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ProbeReport:
        if mode not in {"all", "default_branch"}:
            raise ValueError("mode must be 'all' or 'default_branch'")
        names = tuple(_target_full_name(target) for target in repos)
        if mode == "default_branch":
            result = self._github.batch_default_branch_refs(
                names, concurrency=self._concurrency, on_progress=on_progress
            )
        else:
            result = self._github.batch_repo_refs(
                names, kinds=kinds, concurrency=self._concurrency, on_progress=on_progress
            )
        failures = {
            repo: ProbeFailure(
                kind="missing", reason=_MISSING_REASON, category=ErrorCategory.NOT_FOUND
            )
            for repo in result.missing
        }
        failures.update({failure.full_name: _failure(failure) for failure in result.failures})
        return ProbeReport(
            repos={
                repo_refs.full_name: ProbedRepo(
                    default_branch=repo_refs.default_branch,
                    refs=tuple(
                        GitRef(kind=ref.kind, name=ref.name, sha=ref.sha) for ref in repo_refs.refs
                    ),
                )
                for repo_refs in result.repos
            },
            failures=failures,
            rate_limit_cost=result.rate_limit_cost,
            rate_limit_remaining=result.rate_limit_remaining,
            rate_limit_reset_at=result.rate_limit_reset_at,
        )


def _failure(failure: BatchRepoRefsFailure) -> ProbeFailure:
    if failure.kind == "request":
        # Provenance prefix: distinguishes probe failures from git-fetch
        # failures in `failed <repo>: <reason>` stderr listings.
        return ProbeFailure(
            kind="chunk", reason=f"ref probe failed: {failure.reason}", category=failure.category
        )
    return ProbeFailure(
        kind="transient",
        reason=_format_transient_failure(failure.reason),
        category=ErrorCategory.UNAVAILABLE,
    )


def _format_transient_failure(reason: str) -> str:
    detail = reason.strip() or "unknown transient GitHub GraphQL failure"
    return f"{TRANSIENT_REF_PROBE_FAILURE_PREFIX}{detail}"


def _target_full_name(target: ProbeTarget | str) -> str:
    return target if isinstance(target, str) else target.full_name


def is_transient_ref_probe_failure(reason: str) -> bool:
    """Return whether a refresh failure came from a transient GitHub ref probe."""
    return reason.startswith(TRANSIENT_REF_PROBE_FAILURE_PREFIX)
