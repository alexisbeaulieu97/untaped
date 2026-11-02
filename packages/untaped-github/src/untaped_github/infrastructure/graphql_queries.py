"""The text of the ref probe's GraphQL queries, and how their answers read.

:func:`batch_query` asks for many repositories' refs under aliases ``r0..rN``,
:func:`default_branch_query` for their default branch, and :func:`page_query`
for one repository's next page of refs. :func:`parse_ref_nodes` and
:func:`peel_oid` read the refs back (peeling annotated tags to their commit),
and :class:`RateLimit` is the GraphQL budget each answer reports.
:func:`validate_kinds` and :func:`parse_repo_target` check what a query is
built from, as :class:`RepoTarget`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, NamedTuple, cast

from untaped.sdk import UntapedError, parse_utc
from untaped_github.domain.models import RefKind, RepoRef
from untaped_github.domain.repo_id import split_repo_id

_REF_PREFIXES: dict[RefKind, str] = {"heads": "refs/heads/", "tags": "refs/tags/"}
_PAGE_SIZE = 100
_RATE_LIMIT_FIELD = "rateLimit { cost remaining resetAt }"
# Annotated tags point at a Tag object instead of a commit; peel via
# nested ``target { oid }`` two levels deep (covers tags-of-tags).
_TARGET_FIELD = "target { oid ... on Tag { target { oid ... on Tag { target { oid } } } } }"


class RepoTarget(NamedTuple):
    full_name: str
    owner: str
    name: str


class RateLimit(NamedTuple):
    cost: int | None = None
    remaining: int | None = None
    reset_at: datetime | None = None


def validate_kinds(kinds: Sequence[str]) -> tuple[RefKind, ...]:
    if not kinds:
        raise ValueError("kinds must not be empty")
    for kind in kinds:
        if kind not in _REF_PREFIXES:
            raise ValueError(f"invalid ref kind {kind!r}: expected 'heads' or 'tags'")
    # Dedupe preserving order: GraphQL merges duplicate aliased
    # connections, so a repeated kind would double every ref.
    # Membership in _REF_PREFIXES is exactly the RefKind literal set.
    return tuple(cast("RefKind", kind) for kind in dict.fromkeys(kinds))


def parse_repo_target(value: str) -> RepoTarget:
    # UntapedError, not ValueError: repo strings can originate from user
    # source config (typos), and core's error boundary renders
    # UntapedError as a message instead of a traceback.
    parts = split_repo_id(value)
    if parts is None:
        raise UntapedError(
            f"invalid repository {value!r}: expected 'owner/name'", category="invalid"
        )
    return RepoTarget(value, *parts)


def batch_query(chunk: tuple[RepoTarget, ...], kinds: tuple[RefKind, ...]) -> str:
    fields = " ".join(
        _repository_field(f"r{index}", target, kinds) for index, target in enumerate(chunk)
    )
    return f"{{ {fields} {_RATE_LIMIT_FIELD} }}"


def default_branch_query(chunk: tuple[RepoTarget, ...]) -> str:
    fields = " ".join(
        (
            f"r{index}: repository(owner: {json.dumps(target.owner)}, "
            f"name: {json.dumps(target.name)}) "
            f"{{ nameWithOwner defaultBranchRef {{ name target {{ oid }} }} }}"
        )
        for index, target in enumerate(chunk)
    )
    return f"{{ {fields} {_RATE_LIMIT_FIELD} }}"


def _repository_field(alias: str, target: RepoTarget, kinds: tuple[RefKind, ...]) -> str:
    refs = " ".join(_refs_field(kind) for kind in kinds)
    return (
        f"{alias}: repository(owner: {json.dumps(target.owner)}, "
        f"name: {json.dumps(target.name)}) "
        f"{{ nameWithOwner defaultBranchRef {{ name }} {refs} }}"
    )


def page_query(target: RepoTarget, kind: RefKind, cursor: str) -> str:
    return (
        f"{{ r0: repository(owner: {json.dumps(target.owner)}, "
        f"name: {json.dumps(target.name)}) "
        f"{{ {_refs_field(kind, after=cursor)} }} {_RATE_LIMIT_FIELD} }}"
    )


def _refs_field(kind: RefKind, *, after: str | None = None) -> str:
    cursor = f", after: {json.dumps(after)}" if after is not None else ""
    return (
        f"{kind}: refs(refPrefix: {json.dumps(_REF_PREFIXES[kind])}, "
        f"first: {_PAGE_SIZE}{cursor}) "
        f"{{ pageInfo {{ hasNextPage endCursor }} nodes {{ name {_TARGET_FIELD} }} }}"
    )


def parse_ref_nodes(kind: RefKind, nodes: list[dict[str, Any]]) -> list[RepoRef]:
    # GitHub's schema allows ``Ref.target`` to be null; a ref without a
    # resolvable object is useless to the freshness probe, so skip it.
    return [
        RepoRef(kind=kind, name=node["name"], sha=peel_oid(node["target"]))
        for node in nodes
        if node.get("target") is not None
    ]


def peel_oid(target: dict[str, Any]) -> str:
    """Return the deepest oid: annotated tags nest the commit under ``target``."""
    oid = target.get("oid", "")
    inner = target.get("target")
    while isinstance(inner, dict):
        if inner.get("oid"):
            oid = inner["oid"]
        inner = inner.get("target")
    return str(oid)


def reported_rate_limit(payload: dict[str, Any]) -> RateLimit:
    data = payload.get("data")
    if not isinstance(data, dict):
        return RateLimit()
    rate_limit = data.get("rateLimit")
    if not isinstance(rate_limit, dict):
        return RateLimit()
    cost = rate_limit.get("cost")
    remaining = rate_limit.get("remaining")
    reset_at = parse_utc(rate_limit.get("resetAt"))
    return RateLimit(
        cost=cost if isinstance(cost, int) else None,
        remaining=remaining if isinstance(remaining, int) else None,
        reset_at=reset_at,
    )


def merge_rate_limit(current: RateLimit, latest: RateLimit) -> RateLimit:
    # Chunks run side by side, so the lowest budget and the latest reset win.
    return RateLimit(
        cost=_sum_optional_ints(current.cost, latest.cost),
        remaining=_min_optional(current.remaining, latest.remaining),
        reset_at=_max_optional(current.reset_at, latest.reset_at),
    )


def _min_optional(left: int | None, right: int | None) -> int | None:
    return right if left is None else left if right is None else min(left, right)


def _max_optional(left: datetime | None, right: datetime | None) -> datetime | None:
    return right if left is None else left if right is None else max(left, right)


def _sum_optional_ints(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return left + right
