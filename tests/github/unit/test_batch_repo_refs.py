"""Unit tests for ``GithubClient.batch_repo_refs`` (GraphQL batched ref probe)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from untaped.api import UntapedError
from untaped.capabilities.github.domain.errors import GithubGraphqlError
from untaped.capabilities.github.domain.models import BatchRepoRefsFailure
from untaped.capabilities.github.infrastructure import GithubClient
from untaped.capabilities.github.settings import GithubSettings


def _client(base_url: str = "https://api.github.com") -> GithubClient:
    return GithubClient(GithubSettings(token=SecretStr("ghp_test"), base_url=base_url))


def _ref_node(name: str, target: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "target": target}


def _connection(
    nodes: list[dict[str, Any]],
    *,
    has_next: bool = False,
    end_cursor: str | None = None,
) -> dict[str, Any]:
    return {"pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor}, "nodes": nodes}


def _repo_node(
    full_name: str,
    *,
    default_branch: str | None = "main",
    heads: dict[str, Any] | None = None,
    tags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "nameWithOwner": full_name,
        "defaultBranchRef": {"name": default_branch} if default_branch else None,
    }
    if heads is not None:
        node["heads"] = heads
    if tags is not None:
        node["tags"] = tags
    return node


def _payload(
    repos: dict[str, Any],
    *,
    errors: list[dict[str, Any]] | None = None,
    cost: int = 1,
    remaining: int = 4999,
    reset_at: str = "2026-06-10T00:00:00Z",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "data": {
            **repos,
            "rateLimit": {"cost": cost, "remaining": remaining, "resetAt": reset_at},
        }
    }
    if errors is not None:
        body["errors"] = errors
    return body


def _query(route: respx.Route, call: int = 0) -> str:
    body = json.loads(route.calls[call].request.content)
    query = body["query"]
    assert isinstance(query, str)
    return query


def _is_query_for(request: httpx.Request, repo: str) -> bool:
    query = json.loads(request.content)["query"]
    assert isinstance(query, str)
    return f'name: "{repo}"' in query


def _raise_transport_failure(request: httpx.Request) -> None:
    raise httpx.ConnectError("connect failed", request=request)


def test_batch_repo_refs_returns_refs_default_branch_and_rate_limit() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(
            return_value=httpx.Response(
                200,
                json=_payload(
                    {
                        "r0": _repo_node(
                            "acme/site",
                            heads=_connection([_ref_node("main", {"oid": "c1"})]),
                            tags=_connection(
                                [
                                    # Lightweight tag: target is the commit itself.
                                    _ref_node("v0.9.0", {"oid": "c8"}),
                                    # Annotated tag: peel one level to the commit.
                                    _ref_node("v1.0.0", {"oid": "t1", "target": {"oid": "c9"}}),
                                    # Tag-of-tag: peel two levels to the commit.
                                    _ref_node(
                                        "v1.0.1",
                                        {
                                            "oid": "t2",
                                            "target": {"oid": "t3", "target": {"oid": "c10"}},
                                        },
                                    ),
                                ]
                            ),
                        ),
                        "r1": _repo_node(
                            "acme/empty",
                            default_branch=None,
                            heads=_connection([]),
                            tags=_connection([]),
                        ),
                    },
                    cost=7,
                    remaining=4998,
                ),
            )
        )
        with _client() as client:
            result = client.batch_repo_refs(["acme/site", "acme/empty"])

    assert route.call_count == 1
    site, empty = result.repos
    assert site.full_name == "acme/site"
    assert site.default_branch == "main"
    assert [(ref.kind, ref.name, ref.sha) for ref in site.refs] == [
        ("heads", "main", "c1"),
        ("tags", "v0.9.0", "c8"),
        ("tags", "v1.0.0", "c9"),
        ("tags", "v1.0.1", "c10"),
    ]
    assert empty.full_name == "acme/empty"
    assert empty.default_branch is None
    assert empty.refs == ()
    assert result.missing == ()
    assert result.rate_limit_cost == 7
    assert result.rate_limit_remaining == 4998
    assert result.rate_limit_reset_at is not None
    assert result.rate_limit_reset_at.isoformat() == "2026-06-10T00:00:00+00:00"
    query = _query(route)
    assert 'r0: repository(owner: "acme", name: "site")' in query
    assert 'r1: repository(owner: "acme", name: "empty")' in query
    assert "rateLimit { cost remaining resetAt }" in query


def test_batch_repo_refs_sums_rate_limit_cost_and_keeps_latest_remaining_and_reset() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=_payload(
                        {"r0": _repo_node("acme/a", heads=_connection([]), tags=_connection([]))},
                        cost=2,
                        remaining=4998,
                        reset_at="2026-06-10T00:00:00Z",
                    ),
                ),
                httpx.Response(
                    200,
                    json=_payload(
                        {"r0": _repo_node("acme/b", heads=_connection([]), tags=_connection([]))},
                        cost=3,
                        remaining=4995,
                        reset_at="2026-06-10T01:00:00Z",
                    ),
                ),
            ]
        )
        with _client() as client:
            result = client.batch_repo_refs(["acme/a", "acme/b"], chunk_size=1)

    assert result.rate_limit_cost == 5
    assert result.rate_limit_remaining == 4995
    assert result.rate_limit_reset_at is not None
    assert result.rate_limit_reset_at.isoformat() == "2026-06-10T01:00:00+00:00"


def test_batch_repo_refs_includes_ref_pagination_cost_in_total() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=_payload(
                        {
                            "r0": _repo_node(
                                "acme/site",
                                heads=_connection(
                                    [_ref_node("main", {"oid": "c1"})],
                                    has_next=True,
                                    end_cursor="CUR",
                                ),
                            )
                        },
                        cost=2,
                        remaining=4998,
                    ),
                ),
                httpx.Response(
                    200,
                    json=_payload(
                        {"r0": {"heads": _connection([_ref_node("dev", {"oid": "c2"})])}},
                        cost=1,
                        remaining=4997,
                    ),
                ),
            ]
        )
        with _client() as client:
            result = client.batch_repo_refs(["acme/site"], kinds=("heads",))

    assert result.rate_limit_cost == 3
    assert result.rate_limit_remaining == 4997


def test_batch_default_branch_refs_uses_connection_free_query_and_synthesizes_head_ref() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(
            return_value=httpx.Response(
                200,
                json=_payload(
                    {
                        "r0": {
                            "nameWithOwner": "acme/site",
                            "defaultBranchRef": {"name": "trunk", "target": {"oid": "c1"}},
                        },
                        "r1": {
                            "nameWithOwner": "acme/empty",
                            "defaultBranchRef": None,
                        },
                    },
                    cost=1,
                    remaining=4999,
                ),
            )
        )
        with _client() as client:
            result = client.batch_default_branch_refs(["acme/site", "acme/empty"])

    assert route.call_count == 1
    query = _query(route)
    assert "defaultBranchRef" in query
    assert "refs(" not in query
    site, empty = result.repos
    assert site.full_name == "acme/site"
    assert site.default_branch == "trunk"
    assert [(ref.kind, ref.name, ref.sha) for ref in site.refs] == [("heads", "trunk", "c1")]
    assert empty.full_name == "acme/empty"
    assert empty.default_branch is None
    assert empty.refs == ()
    assert result.rate_limit_cost == 1


def test_batch_default_branch_refs_retries_5xx_same_chunk_before_splitting() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(
            side_effect=[
                httpx.Response(502, text="Bad Gateway"),
                httpx.Response(
                    200,
                    json=_payload(
                        {
                            "r0": {
                                "nameWithOwner": "acme/a",
                                "defaultBranchRef": {"name": "main", "target": {"oid": "sha-a"}},
                            },
                            "r1": {
                                "nameWithOwner": "acme/b",
                                "defaultBranchRef": {"name": "main", "target": {"oid": "sha-b"}},
                            },
                        }
                    ),
                ),
            ]
        )
        with _client() as client:
            result = client.batch_default_branch_refs(["acme/a", "acme/b"], chunk_size=2)

    assert route.call_count == 2
    assert 'name: "a"' in _query(route, 0)
    assert 'name: "b"' in _query(route, 0)
    assert _query(route, 1) == _query(route, 0)
    assert "refs(" not in _query(route, 1)
    assert [(repo.full_name, repo.refs[0].sha) for repo in result.repos] == [
        ("acme/a", "sha-a"),
        ("acme/b", "sha-b"),
    ]
    assert result.failures == ()


def test_batch_default_branch_refs_adaptively_isolates_payload_specific_5xxs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_query_for(request, "bad"):
            return httpx.Response(502, text="Bad Gateway")
        if _is_query_for(request, "a"):
            repo = "acme/a"
            sha = "sha-a"
        else:
            repo = "acme/c"
            sha = "sha-c"
        return httpx.Response(
            200,
            json=_payload(
                {
                    "r0": {
                        "nameWithOwner": repo,
                        "defaultBranchRef": {"name": "main", "target": {"oid": sha}},
                    }
                }
            ),
        )

    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(side_effect=handler)
        with _client() as client:
            result = client.batch_default_branch_refs(
                ["acme/a", "acme/bad", "acme/c"], chunk_size=3
            )

    assert route.call_count == 11
    assert [(repo.full_name, repo.refs[0].sha) for repo in result.repos] == [
        ("acme/a", "sha-a"),
        ("acme/c", "sha-c"),
    ]
    assert [
        (failure.full_name, failure.kind, failure.status_code) for failure in result.failures
    ] == [("acme/bad", "server_error", 502)]
    assert all("refs(" not in _query(route, index) for index in range(route.call_count))


def test_batch_default_branch_refs_full_outage_reports_original_chunk_with_bounded_split() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(return_value=httpx.Response(502, text="Bad Gateway"))
        with _client() as client:
            result = client.batch_default_branch_refs(
                ["acme/a", "acme/b", "acme/c", "acme/d"], chunk_size=4
            )

    assert route.call_count == 21
    assert result.repos == ()
    assert [failure.full_name for failure in result.failures] == [
        "acme/a",
        "acme/b",
        "acme/c",
        "acme/d",
    ]


def test_batch_default_branch_refs_single_repo_5xx_becomes_transient_failure() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(return_value=httpx.Response(502, text="Bad Gateway"))
        with _client() as client:
            result = client.batch_default_branch_refs(["acme/a"])

    assert route.call_count == 3
    assert result.repos == ()
    assert result.failures == (
        BatchRepoRefsFailure(
            full_name="acme/a",
            reason="HTTP 502 for https://api.github.com/graphql",
            kind="server_error",
            status_code=502,
            url="https://api.github.com/graphql",
        ),
    )


def test_batch_repo_refs_chunks_requests_by_chunk_size() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=_payload(
                        {
                            "r0": _repo_node("acme/a", heads=_connection([]), tags=_connection([])),
                            "r1": _repo_node("acme/b", heads=_connection([]), tags=_connection([])),
                        }
                    ),
                ),
                httpx.Response(
                    200,
                    json=_payload(
                        {"r0": _repo_node("acme/c", heads=_connection([]), tags=_connection([]))},
                        remaining=4997,
                    ),
                ),
            ]
        )
        with _client() as client:
            result = client.batch_repo_refs(["acme/a", "acme/b", "acme/c"], chunk_size=2)

    assert route.call_count == 2
    first = _query(route, 0)
    assert 'name: "a"' in first
    assert 'name: "b"' in first
    assert 'name: "c"' not in first
    second = _query(route, 1)
    assert 'r0: repository(owner: "acme", name: "c")' in second
    assert [repo.full_name for repo in result.repos] == ["acme/a", "acme/b", "acme/c"]
    assert result.rate_limit_remaining == 4997


def test_batch_repo_refs_follows_ref_pagination_cursor() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=_payload(
                        {
                            "r0": _repo_node(
                                "acme/site",
                                heads=_connection(
                                    [_ref_node("main", {"oid": "c1"})],
                                    has_next=True,
                                    end_cursor="CUR",
                                ),
                            )
                        },
                        remaining=4990,
                    ),
                ),
                httpx.Response(
                    200,
                    json=_payload(
                        {"r0": {"heads": _connection([_ref_node("dev", {"oid": "c2"})])}},
                        remaining=4989,
                    ),
                ),
            ]
        )
        with _client() as client:
            result = client.batch_repo_refs(["acme/site"], kinds=("heads",))

    assert route.call_count == 2
    assert 'after: "CUR"' in _query(route, 1)
    (site,) = result.repos
    assert [(ref.name, ref.sha) for ref in site.refs] == [("main", "c1"), ("dev", "c2")]
    assert result.rate_limit_remaining == 4989


def test_batch_repo_refs_reports_transient_ref_pagination_failure_per_repo() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=_payload(
                        {
                            "r0": _repo_node(
                                "acme/paged",
                                heads=_connection(
                                    [_ref_node("main", {"oid": "c1"})],
                                    has_next=True,
                                    end_cursor="CUR",
                                ),
                                tags=_connection([]),
                            ),
                            "r1": _repo_node(
                                "acme/ok",
                                heads=_connection([_ref_node("main", {"oid": "ok"})]),
                                tags=_connection([]),
                            ),
                        }
                    ),
                ),
                httpx.Response(502, text="Bad Gateway"),
                httpx.Response(502, text="Bad Gateway"),
                httpx.Response(502, text="Bad Gateway"),
            ]
        )
        with _client() as client:
            result = client.batch_repo_refs(["acme/paged", "acme/ok"], kinds=("heads",))

    assert route.call_count == 4
    assert all('after: "CUR"' in _query(route, call) for call in (1, 2, 3))
    assert [repo.full_name for repo in result.repos] == ["acme/ok"]
    assert [(repo.refs[0].name, repo.refs[0].sha) for repo in result.repos] == [("main", "ok")]
    assert [
        (failure.full_name, failure.kind, failure.status_code) for failure in result.failures
    ] == [("acme/paged", "server_error", 502)]


def test_batch_repo_refs_collects_not_found_repos_into_missing() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(
            return_value=httpx.Response(
                200,
                json=_payload(
                    {
                        "r0": _repo_node("acme/site", heads=_connection([]), tags=_connection([])),
                        "r1": None,
                    },
                    errors=[
                        {
                            "type": "NOT_FOUND",
                            "path": ["r1"],
                            "message": "Could not resolve to a Repository",
                        }
                    ],
                ),
            )
        )
        with _client() as client:
            result = client.batch_repo_refs(["acme/site", "acme/gone"])

    assert [repo.full_name for repo in result.repos] == ["acme/site"]
    assert result.missing == ("acme/gone",)


def test_batch_repo_refs_skips_refs_with_null_target() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(
            return_value=httpx.Response(
                200,
                json=_payload(
                    {
                        "r0": _repo_node(
                            "acme/site",
                            heads=_connection(
                                [
                                    _ref_node("main", {"oid": "c1"}),
                                    # GitHub's schema allows Ref.target to be
                                    # null; such a ref has no resolvable object.
                                    {"name": "broken", "target": None},
                                    _ref_node("dev", {"oid": "c2"}),
                                ]
                            ),
                        )
                    }
                ),
            )
        )
        with _client() as client:
            result = client.batch_repo_refs(["acme/site"], kinds=("heads",))

    (site,) = result.repos
    assert [(ref.name, ref.sha) for ref in site.refs] == [("main", "c1"), ("dev", "c2")]


def test_batch_repo_refs_dedupes_repeated_kinds() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(
            return_value=httpx.Response(
                200,
                json=_payload(
                    {
                        "r0": _repo_node(
                            "acme/site",
                            heads=_connection([_ref_node("main", {"oid": "c1"})]),
                        )
                    }
                ),
            )
        )
        with _client() as client:
            result = client.batch_repo_refs(["acme/site"], kinds=("heads", "heads"))

    # The duplicate kind is dropped: one connection in the query, and the
    # refs are not collected twice.
    assert _query(route).count("refs/heads/") == 1
    (site,) = result.repos
    assert [(ref.name, ref.sha) for ref in site.refs] == [("main", "c1")]


def test_batch_repo_refs_collects_forbidden_repos_into_missing() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(
            return_value=httpx.Response(
                200,
                json=_payload(
                    {
                        "r0": _repo_node("acme/site", heads=_connection([]), tags=_connection([])),
                        "r1": None,
                    },
                    errors=[
                        {
                            "type": "FORBIDDEN",
                            "path": ["r1"],
                            "message": "Resource not accessible",
                        }
                    ],
                ),
            )
        )
        with _client() as client:
            result = client.batch_repo_refs(["acme/site", "acme/private"])

    assert [repo.full_name for repo in result.repos] == ["acme/site"]
    assert result.missing == ("acme/private",)


def test_batch_repo_refs_raises_when_repo_lost_during_ref_pagination() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json=_payload(
                        {
                            "r0": _repo_node(
                                "acme/site",
                                heads=_connection(
                                    [_ref_node("main", {"oid": "c1"})],
                                    has_next=True,
                                    end_cursor="CUR",
                                ),
                            )
                        }
                    ),
                ),
                # The repo resolved in the batch query but vanishes
                # (deleted mid-probe) before the pagination follow-up.
                httpx.Response(
                    200,
                    json=_payload(
                        {"r0": None},
                        errors=[
                            {
                                "type": "NOT_FOUND",
                                "path": ["r0"],
                                "message": "Could not resolve to a Repository",
                            }
                        ],
                    ),
                ),
            ]
        )
        with (
            _client() as client,
            pytest.raises(UntapedError, match="lost access to acme/site during ref pagination"),
        ):
            client.batch_repo_refs(["acme/site"], kinds=("heads",))


def test_batch_repo_refs_raises_rate_limited_graphql_error() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": None,
                    "errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}],
                },
            )
        )
        with _client() as client, pytest.raises(GithubGraphqlError) as exc_info:
            client.batch_repo_refs(["acme/site"])

    assert exc_info.value.kind == "rate_limited"
    assert "API rate limit exceeded" in str(exc_info.value)


def test_batch_repo_refs_raises_unknown_graphql_error() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": None,
                    "errors": [{"type": "SOMETHING_ELSE", "message": "GraphQL blew up"}],
                },
            )
        )
        with _client() as client, pytest.raises(GithubGraphqlError) as exc_info:
            client.batch_repo_refs(["acme/site"])

    assert exc_info.value.kind == "unknown"
    assert "GraphQL blew up" in str(exc_info.value)


def test_batch_repo_refs_raises_forbidden_for_nested_graphql_error_path() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(
            return_value=httpx.Response(
                200,
                json=_payload(
                    {"r0": None},
                    errors=[
                        {
                            "type": "FORBIDDEN",
                            "path": ["r0", "refs"],
                            "message": "Resource not accessible by personal access token",
                        }
                    ],
                ),
            )
        )
        with _client() as client, pytest.raises(GithubGraphqlError) as exc_info:
            client.batch_repo_refs(["acme/site"])

    assert exc_info.value.kind == "forbidden"
    assert "Resource not accessible" in str(exc_info.value)


def test_batch_repo_refs_raises_on_null_repo_without_error() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(return_value=httpx.Response(200, json=_payload({"r0": None})))
        with _client() as client, pytest.raises(UntapedError, match="acme/site"):
            client.batch_repo_refs(["acme/site"])


def test_batch_repo_refs_derives_ghe_graphql_endpoint() -> None:
    with respx.mock(base_url="https://ghe.example.com") as mock:
        route = mock.post("/api/graphql").mock(
            return_value=httpx.Response(
                200,
                json=_payload(
                    {"r0": _repo_node("acme/site", heads=_connection([]), tags=_connection([]))}
                ),
            )
        )
        with _client(base_url="https://ghe.example.com/api/v3") as client:
            result = client.batch_repo_refs(["acme/site"])

    assert route.call_count == 1
    assert result.repos[0].full_name == "acme/site"


def test_batch_repo_refs_heads_only_omits_tags_from_query() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(
            return_value=httpx.Response(
                200,
                json=_payload({"r0": _repo_node("acme/site", heads=_connection([]))}),
            )
        )
        with _client() as client:
            result = client.batch_repo_refs(["acme/site"], kinds=("heads",))

    query = _query(route)
    assert "refs/heads/" in query
    assert "refs/tags/" not in query
    assert result.repos[0].refs == ()


def test_batch_repo_refs_retries_5xx_same_chunk_before_splitting() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(
            side_effect=[
                httpx.Response(502, text="Bad Gateway"),
                httpx.Response(
                    200,
                    json=_payload(
                        {
                            "r0": _repo_node("acme/a", heads=_connection([]), tags=_connection([])),
                            "r1": _repo_node("acme/b", heads=_connection([]), tags=_connection([])),
                        }
                    ),
                ),
            ]
        )
        with _client() as client:
            result = client.batch_repo_refs(["acme/a", "acme/b"])

    assert route.call_count == 2
    assert _query(route, 1) == _query(route, 0)
    assert [repo.full_name for repo in result.repos] == ["acme/a", "acme/b"]
    assert result.failures == ()


def test_batch_repo_refs_adaptively_isolates_payload_specific_5xxs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_query_for(request, "bad"):
            return httpx.Response(502, text="Bad Gateway")
        repo = "acme/a" if _is_query_for(request, "a") else "acme/c"
        return httpx.Response(
            200,
            json=_payload({"r0": _repo_node(repo, heads=_connection([]), tags=_connection([]))}),
        )

    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(side_effect=handler)
        with _client() as client:
            result = client.batch_repo_refs(["acme/a", "acme/bad", "acme/c"], chunk_size=3)

    assert route.call_count == 11
    assert [repo.full_name for repo in result.repos] == ["acme/a", "acme/c"]
    assert [
        (failure.full_name, failure.kind, failure.status_code) for failure in result.failures
    ] == [("acme/bad", "server_error", 502)]


def test_batch_repo_refs_isolates_multiple_bad_repos_in_different_halves() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_query_for(request, "bad0") or _is_query_for(request, "bad3"):
            return httpx.Response(502, text="Bad Gateway")
        repo = "acme/ok1" if _is_query_for(request, "ok1") else "acme/ok2"
        return httpx.Response(
            200,
            json=_payload({"r0": _repo_node(repo, heads=_connection([]), tags=_connection([]))}),
        )

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(side_effect=handler)
        with _client() as client:
            result = client.batch_repo_refs(
                ["acme/bad0", "acme/ok1", "acme/ok2", "acme/bad3"], chunk_size=4
            )

    assert [repo.full_name for repo in result.repos] == ["acme/ok1", "acme/ok2"]
    assert [failure.full_name for failure in result.failures] == ["acme/bad0", "acme/bad3"]


def test_batch_repo_refs_applies_no_progress_lookahead_recursively() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if _is_query_for(request, "bad4") or _is_query_for(request, "bad7"):
            return httpx.Response(502, text="Bad Gateway")
        for index in range(7):
            if _is_query_for(request, f"ok{index}"):
                return httpx.Response(
                    200,
                    json=_payload(
                        {
                            f"r{alias}": _repo_node(
                                f"acme/ok{index + alias}",
                                heads=_connection([]),
                                tags=_connection([]),
                            )
                            for alias in range(4)
                            if _is_query_for(request, f"ok{index + alias}")
                        }
                    ),
                )
        raise AssertionError("expected at least one ok repo in successful query")

    repos = [
        "acme/ok0",
        "acme/ok1",
        "acme/ok2",
        "acme/ok3",
        "acme/bad4",
        "acme/ok5",
        "acme/ok6",
        "acme/bad7",
    ]

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(side_effect=handler)
        with _client() as client:
            result = client.batch_repo_refs(repos, chunk_size=8)

    assert [repo.full_name for repo in result.repos] == [
        "acme/ok0",
        "acme/ok1",
        "acme/ok2",
        "acme/ok3",
        "acme/ok5",
        "acme/ok6",
    ]
    assert [failure.full_name for failure in result.failures] == ["acme/bad4", "acme/bad7"]


def test_batch_repo_refs_full_outage_reports_original_chunk_with_bounded_split() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(return_value=httpx.Response(502, text="Bad Gateway"))
        with _client() as client:
            result = client.batch_repo_refs(["acme/a", "acme/b", "acme/c", "acme/d"], chunk_size=4)

    assert route.call_count == 21
    assert result.repos == ()
    assert [failure.full_name for failure in result.failures] == [
        "acme/a",
        "acme/b",
        "acme/c",
        "acme/d",
    ]


def test_batch_repo_refs_single_repo_5xx_becomes_transient_failure() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(return_value=httpx.Response(502, text="Bad Gateway"))
        with _client() as client:
            result = client.batch_repo_refs(["acme/a"])

    assert route.call_count == 3
    assert result.repos == ()
    assert result.failures == (
        BatchRepoRefsFailure(
            full_name="acme/a",
            reason="HTTP 502 for https://api.github.com/graphql",
            kind="server_error",
            status_code=502,
            url="https://api.github.com/graphql",
        ),
    )


def test_batch_repo_refs_retries_transport_error_same_chunk_before_splitting() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            _raise_transport_failure(request)
        return httpx.Response(
            200,
            json=_payload(
                {
                    "r0": _repo_node("acme/a", heads=_connection([]), tags=_connection([])),
                    "r1": _repo_node("acme/b", heads=_connection([]), tags=_connection([])),
                }
            ),
        )

    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(side_effect=handler)
        with _client() as client:
            result = client.batch_repo_refs(["acme/a", "acme/b"])

    assert route.call_count == 2
    assert [repo.full_name for repo in result.repos] == ["acme/a", "acme/b"]
    assert result.failures == ()


def test_batch_repo_refs_single_repo_transport_error_becomes_transient_failure() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(side_effect=_raise_transport_failure)
        with _client() as client:
            result = client.batch_repo_refs(["acme/a"])

    assert route.call_count == 3
    assert result.repos == ()
    assert [
        (failure.full_name, failure.kind, failure.status_code) for failure in result.failures
    ] == [("acme/a", "transport", None)]
    assert "connect failed" in result.failures[0].reason


def test_batch_repo_refs_transport_outage_stops_at_request_bound() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(side_effect=_raise_transport_failure)
        with _client() as client:
            result = client.batch_repo_refs(["acme/a", "acme/b", "acme/c", "acme/d"], chunk_size=4)

    assert route.call_count == 21
    assert result.repos == ()
    assert [failure.kind for failure in result.failures] == [
        "transport",
        "transport",
        "transport",
        "transport",
    ]


def test_batch_repo_refs_does_not_retry_4xx() -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(
            side_effect=[httpx.Response(401, json={"message": "Bad credentials"})]
        )
        with _client() as client, pytest.raises(GithubGraphqlError) as exc_info:
            client.batch_repo_refs(["acme/a", "acme/b"])

    assert route.call_count == 1
    assert exc_info.value.kind == "auth"
    assert "Bad credentials" in str(exc_info.value)


@pytest.mark.parametrize(
    ("response", "kind", "message"),
    [
        (
            httpx.Response(
                403,
                json={"message": "API rate limit exceeded for user ID 123."},
            ),
            "rate_limited",
            "API rate limit exceeded",
        ),
        (
            httpx.Response(
                403,
                json={
                    "message": (
                        "You have exceeded a secondary rate limit. "
                        "Please wait a few minutes before you try again."
                    )
                },
            ),
            "secondary_rate_limited",
            "secondary rate limit",
        ),
        (
            httpx.Response(
                429,
                json={"message": "You have exceeded a secondary rate limit."},
            ),
            "secondary_rate_limited",
            "secondary rate limit",
        ),
        (
            httpx.Response(
                403,
                json={"message": "Resource not accessible by personal access token"},
            ),
            "forbidden",
            "Resource not accessible",
        ),
    ],
)
def test_batch_repo_refs_classifies_graphql_http_access_errors(
    response: httpx.Response,
    kind: str,
    message: str,
) -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(side_effect=[response])
        with _client() as client, pytest.raises(GithubGraphqlError) as exc_info:
            client.batch_repo_refs(["acme/a", "acme/b"])

    assert route.call_count == 1
    assert exc_info.value.kind == kind
    assert message in str(exc_info.value)
    assert exc_info.value.status_code == response.status_code
    assert exc_info.value.body


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            {"message": "Resource not accessible by personal access token"},
            "Resource not accessible by personal access token",
        ),
        (
            {"error": "Resource not accessible by personal access token"},
            "Resource not accessible by personal access token",
        ),
        (
            {"detail": "Resource not accessible by personal access token"},
            "Resource not accessible by personal access token",
        ),
        (
            {"errors": [{"message": "Resource not accessible by personal access token"}]},
            "Resource not accessible by personal access token",
        ),
    ],
)
def test_batch_repo_refs_extracts_graphql_http_error_messages_from_common_shapes(
    body: dict[str, object],
    message: str,
) -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        mock.post("/graphql").mock(return_value=httpx.Response(403, json=body))
        with _client() as client, pytest.raises(GithubGraphqlError) as exc_info:
            client.batch_repo_refs(["acme/a"])

    assert exc_info.value.kind == "forbidden"
    assert str(exc_info.value) == f"github graphql access forbidden: {message}"


@pytest.mark.parametrize("bad", ["site", "acme/", "/site", "acme/site/extra", ""])
def test_batch_repo_refs_rejects_invalid_repo_strings(bad: str) -> None:
    # UntapedError, not ValueError: repo strings can come from user
    # source config, and core ``report_errors`` renders UntapedError
    # cleanly instead of as a traceback.
    with _client() as client, pytest.raises(UntapedError, match="owner/name"):
        client.batch_repo_refs([bad])


def test_batch_repo_refs_rejects_unknown_kind() -> None:
    with _client() as client, pytest.raises(ValueError, match="releases"):
        client.batch_repo_refs(["acme/site"], kinds=("heads", "releases"))


def test_batch_repo_refs_rejects_empty_kinds() -> None:
    with _client() as client, pytest.raises(ValueError, match="kinds"):
        client.batch_repo_refs(["acme/site"], kinds=())


def test_batch_repo_refs_rejects_non_positive_chunk_size() -> None:
    with _client() as client, pytest.raises(ValueError, match="chunk_size"):
        client.batch_repo_refs(["acme/site"], chunk_size=0)


def test_batch_repo_refs_with_no_repos_makes_no_requests() -> None:
    with respx.mock(base_url="https://api.github.com"), _client() as client:
        result = client.batch_repo_refs([])

    assert result.repos == ()
    assert result.missing == ()
    assert result.rate_limit_remaining is None
