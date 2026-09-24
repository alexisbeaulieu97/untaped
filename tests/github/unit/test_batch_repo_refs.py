"""``GithubClient.batch_repo_refs`` / ``batch_default_branch_refs``: the GraphQL ref probe."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from untaped.capabilities.github.domain.errors import GithubGraphqlError
from untaped.capabilities.github.domain.models import BatchRepoRefsResult
from untaped.capabilities.github.infrastructure import GithubClient
from untaped.capabilities.github.settings import GithubSettings
from untaped.capability_api import UntapedError

Reply = httpx.Response | Callable[[httpx.Request], httpx.Response] | Exception


def _probe(
    replies: Reply | Sequence[Reply],
    repos: list[str],
    *,
    method: str = "batch_repo_refs",
    base_url: str = "https://api.github.com",
    endpoint: str = "/graphql",
    **kwargs: Any,
) -> tuple[BatchRepoRefsResult, list[str]]:
    """Run one probe against mocked GraphQL replies; return the result and each query sent."""
    with respx.mock(base_url=base_url.removesuffix("/api/v3"), assert_all_called=False) as mock:
        route = mock.post(endpoint)
        if isinstance(replies, httpx.Response):
            route.mock(return_value=replies)
        else:
            route.mock(side_effect=replies)
        client = GithubClient(GithubSettings(token=SecretStr("ghp_test"), base_url=base_url))
        with client:
            result = getattr(client, method)(repos, **kwargs)
        queries = [json.loads(call.request.content)["query"] for call in route.calls]
    return result, queries


def _raises(replies: Reply | Sequence[Reply], repos: list[str], **kwargs: Any) -> Any:
    with pytest.raises((GithubGraphqlError, UntapedError)) as exc_info:
        _probe(replies, repos, **kwargs)
    return exc_info.value


def _connection(
    nodes: list[dict[str, Any]], *, has_next: bool = False, end_cursor: str | None = None
) -> dict[str, Any]:
    return {"pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor}, "nodes": nodes}


def _ref(name: str, oid: str) -> dict[str, Any]:
    return {"name": name, "target": {"oid": oid}}


def _repo_node(
    full_name: str, *, default_branch: str | None = "main", **connections: dict[str, Any]
) -> dict[str, Any]:
    return {
        "nameWithOwner": full_name,
        "defaultBranchRef": {"name": default_branch} if default_branch else None,
        "heads": _connection([]),
        "tags": _connection([]),
        **connections,
    }


def _ok(*nodes: dict[str, Any] | None, **rate: Any) -> httpx.Response:
    return httpx.Response(
        200, json=_payload({f"r{i}": node for i, node in enumerate(nodes)}, **rate)
    )


def _payload(
    repos: dict[str, Any],
    *,
    errors: list[dict[str, Any]] | None = None,
    cost: int = 1,
    remaining: int = 4999,
    reset_at: str = "2026-06-10T00:00:00Z",
) -> dict[str, Any]:
    rate_limit = {"cost": cost, "remaining": remaining, "resetAt": reset_at}
    body: dict[str, Any] = {"data": {**repos, "rateLimit": rate_limit}}
    if errors is not None:
        body["errors"] = errors
    return body


def _bad_gateway() -> httpx.Response:
    return httpx.Response(502, text="Bad Gateway")


def _always_bad_gateway(request: httpx.Request) -> httpx.Response:
    return _bad_gateway()


def _connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connect failed", request=request)


def _names(result: BatchRepoRefsResult) -> tuple[list[str], list[str]]:
    return [repo.full_name for repo in result.repos], [f.full_name for f in result.failures]


def test_batch_repo_refs_returns_peeled_refs_default_branch_and_rate_limit() -> None:
    tags = _connection(
        [
            _ref("v0.9.0", "c8"),  # lightweight: target is the commit
            {"name": "v1.0.0", "target": {"oid": "t1", "target": {"oid": "c9"}}},  # annotated
            {
                "name": "v1.0.1",  # tag of a tag: peel twice
                "target": {"oid": "t2", "target": {"oid": "t3", "target": {"oid": "c10"}}},
            },
            {"name": "broken", "target": None},  # GitHub allows a null target: skipped
        ]
    )
    reply = _ok(
        _repo_node("acme/site", heads=_connection([_ref("main", "c1")]), tags=tags),
        _repo_node("acme/empty", default_branch=None),
        cost=7,
        remaining=4998,
    )

    result, [query] = _probe(reply, ["acme/site", "acme/empty"])

    site, empty = result.repos
    assert (site.full_name, site.default_branch) == ("acme/site", "main")
    assert [(ref.kind, ref.name, ref.sha) for ref in site.refs] == [
        ("heads", "main", "c1"),
        ("tags", "v0.9.0", "c8"),
        ("tags", "v1.0.0", "c9"),
        ("tags", "v1.0.1", "c10"),
    ]
    assert (empty.full_name, empty.default_branch, empty.refs) == ("acme/empty", None, ())
    assert result.missing == ()
    assert (result.rate_limit_cost, result.rate_limit_remaining) == (7, 4998)
    assert str(result.rate_limit_reset_at) == "2026-06-10 00:00:00+00:00"
    assert 'r0: repository(owner: "acme", name: "site")' in query
    assert 'r1: repository(owner: "acme", name: "empty")' in query
    assert "rateLimit { cost remaining resetAt }" in query


def test_batch_repo_refs_chunks_requests_and_merges_rate_limits() -> None:
    result, (first, second) = _probe(
        [
            _ok(_repo_node("acme/a"), _repo_node("acme/b"), cost=2, remaining=4998),
            _ok(_repo_node("acme/c"), cost=3, remaining=4995, reset_at="2026-06-10T01:00:00Z"),
        ],
        ["acme/a", "acme/b", "acme/c"],
        chunk_size=2,
    )

    assert 'name: "a"' in first and 'name: "b"' in first and 'name: "c"' not in first
    assert 'r0: repository(owner: "acme", name: "c")' in second
    assert _names(result) == (["acme/a", "acme/b", "acme/c"], [])
    # Cost adds up; remaining and reset come from the latest response.
    assert (result.rate_limit_cost, result.rate_limit_remaining) == (5, 4995)
    assert str(result.rate_limit_reset_at) == "2026-06-10 01:00:00+00:00"


def test_batch_repo_refs_follows_ref_pagination_cursor() -> None:
    paged = _connection([_ref("main", "c1")], has_next=True, end_cursor="CUR")

    result, (_, page) = _probe(
        [
            _ok(_repo_node("acme/site", heads=paged), cost=2, remaining=4990),
            httpx.Response(
                200,
                json=_payload({"r0": {"heads": _connection([_ref("dev", "c2")])}}, remaining=4989),
            ),
        ],
        ["acme/site"],
        kinds=("heads",),
    )

    assert 'after: "CUR"' in page
    assert [(ref.name, ref.sha) for ref in result.repos[0].refs] == [("main", "c1"), ("dev", "c2")]
    assert (result.rate_limit_cost, result.rate_limit_remaining) == (3, 4989)


def test_batch_repo_refs_dedupes_kinds_and_omits_unrequested_connections() -> None:
    result, [query] = _probe(
        _ok(_repo_node("acme/site", heads=_connection([_ref("main", "c1")]))),
        ["acme/site"],
        kinds=("heads", "heads"),
    )

    assert query.count("refs/heads/") == 1
    assert "refs/tags/" not in query
    assert [(ref.name, ref.sha) for ref in result.repos[0].refs] == [("main", "c1")]


@pytest.mark.parametrize(
    ("error_type", "message"),
    [("NOT_FOUND", "Could not resolve to a Repository"), ("FORBIDDEN", "Resource not accessible")],
)
def test_batch_repo_refs_collects_inaccessible_repos_into_missing(
    error_type: str, message: str
) -> None:
    reply = httpx.Response(
        200,
        json=_payload(
            {"r0": _repo_node("acme/site"), "r1": None},
            errors=[{"type": error_type, "path": ["r1"], "message": message}],
        ),
    )

    result, _ = _probe(reply, ["acme/site", "acme/gone"])

    assert _names(result) == (["acme/site"], [])
    assert result.missing == ("acme/gone",)


@pytest.mark.parametrize(
    ("body", "kind", "message"),
    [
        (
            {
                "data": None,
                "errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}],
            },
            "rate_limited",
            "API rate limit exceeded",
        ),
        (
            {"data": None, "errors": [{"type": "SOMETHING_ELSE", "message": "GraphQL blew up"}]},
            "unknown",
            "GraphQL blew up",
        ),
        (
            _payload(
                {"r0": None},
                errors=[
                    {
                        "type": "FORBIDDEN",
                        "path": ["r0", "refs"],
                        "message": "Resource not accessible by personal access token",
                    }
                ],
            ),
            "forbidden",
            "Resource not accessible",
        ),
    ],
    ids=["rate-limited", "unknown", "nested-path-forbidden"],
)
def test_batch_repo_refs_raises_global_graphql_errors(
    body: dict[str, Any], kind: str, message: str
) -> None:
    error = _raises(httpx.Response(200, json=body), ["acme/site"])

    assert isinstance(error, GithubGraphqlError)
    assert error.kind == kind
    assert message in str(error)


def test_batch_repo_refs_raises_on_unexplained_null_or_repo_lost_mid_pagination() -> None:
    lost = httpx.Response(
        200,
        json=_payload(
            {"r0": None},
            errors=[{"type": "NOT_FOUND", "path": ["r0"], "message": "Could not resolve"}],
        ),
    )
    paged = _connection([_ref("main", "c1")], has_next=True, end_cursor="CUR")

    unexplained = _raises(_ok(None), ["acme/site"])
    # The repo resolved in the batch query, then vanished before the next page.
    vanished = _raises([_ok(_repo_node("acme/site", heads=paged)), lost], ["acme/site"])

    assert "github graphql returned null for acme/site" in str(unexplained)
    assert "lost access to acme/site during ref pagination" in str(vanished)


def test_batch_repo_refs_derives_ghe_graphql_endpoint() -> None:
    result, _ = _probe(
        _ok(_repo_node("acme/site")),
        ["acme/site"],
        base_url="https://ghe.example.com/api/v3",
        endpoint="/api/graphql",
    )

    assert _names(result) == (["acme/site"], [])


@pytest.mark.parametrize("failure", [_bad_gateway(), httpx.ConnectError("connect failed")])
def test_transient_failure_retries_the_same_chunk_before_splitting(failure: Reply) -> None:
    result, queries = _probe(
        [failure, _ok(_repo_node("acme/a"), _repo_node("acme/b"))], ["acme/a", "acme/b"]
    )

    assert len(queries) == 2
    assert queries[1] == queries[0]
    assert _names(result) == (["acme/a", "acme/b"], [])


@pytest.mark.parametrize(
    ("failure", "kind", "status", "reason"),
    [
        (_always_bad_gateway, "server_error", 502, "HTTP 502 for https://api.github.com/graphql"),
        (_connect_error, "transport", None, "connect failed"),
    ],
)
def test_single_repo_transient_failure_becomes_a_failure_row_after_retries(
    failure: Reply, kind: str, status: int | None, reason: str
) -> None:
    result, queries = _probe(failure, ["acme/a"])

    assert len(queries) == 3
    assert result.repos == ()
    [row] = result.failures
    assert (row.full_name, row.kind, row.status_code) == ("acme/a", kind, status)
    assert reason in row.reason


@pytest.mark.parametrize("failure", [_always_bad_gateway, _connect_error])
def test_full_outage_reports_every_repo_within_a_bounded_number_of_requests(
    failure: Reply,
) -> None:
    result, queries = _probe(failure, ["acme/a", "acme/b", "acme/c", "acme/d"], chunk_size=4)

    assert len(queries) == 21
    assert _names(result) == ([], ["acme/a", "acme/b", "acme/c", "acme/d"])


def _fail_for(
    *bad: str, method: str = "batch_repo_refs"
) -> Callable[[httpx.Request], httpx.Response]:
    """Answer 502 to any query naming a ``bad`` repo, else resolve every aliased repo."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if any(f'name: "{name}"' in query for name in bad):
            return _bad_gateway()
        names = [part.split('"')[0] for part in query.split('name: "')[1:]]
        if method == "batch_default_branch_refs":
            nodes = [
                {
                    "nameWithOwner": f"acme/{name}",
                    "defaultBranchRef": {"name": "main", "target": {"oid": name}},
                }
                for name in names
            ]
        else:
            nodes = [_repo_node(f"acme/{name}") for name in names]
        return _ok(*nodes)

    return handler


@pytest.mark.parametrize(
    ("repos", "bad", "method", "requests"),
    [
        (["a", "bad", "c"], ["bad"], "batch_repo_refs", 11),
        (["a", "bad", "c"], ["bad"], "batch_default_branch_refs", 11),
        (["bad0", "ok1", "ok2", "bad3"], ["bad0", "bad3"], "batch_repo_refs", None),
        (
            ["ok0", "ok1", "ok2", "ok3", "bad4", "ok5", "ok6", "bad7"],
            ["bad4", "bad7"],
            "batch_repo_refs",
            None,
        ),
    ],
    ids=["one-bad", "one-bad-default-branch", "bad-in-both-halves", "recursive-lookahead"],
)
def test_adaptive_splitting_isolates_payload_specific_5xxs(
    repos: list[str], bad: list[str], method: str, requests: int | None
) -> None:
    result, queries = _probe(
        _fail_for(*bad, method=method),
        [f"acme/{name}" for name in repos],
        method=method,
        chunk_size=len(repos),
    )

    good = [f"acme/{name}" for name in repos if name not in bad]
    assert _names(result) == (good, [f"acme/{name}" for name in bad])
    assert {failure.kind for failure in result.failures} == {"server_error"}
    assert requests is None or len(queries) == requests
    if method == "batch_default_branch_refs":
        assert all("refs(" not in query for query in queries)


def test_batch_default_branch_refs_uses_connection_free_query_and_synthesizes_head_ref() -> None:
    reply = _ok(
        {
            "nameWithOwner": "acme/site",
            "defaultBranchRef": {"name": "trunk", "target": {"oid": "c1"}},
        },
        {"nameWithOwner": "acme/empty", "defaultBranchRef": None},
    )

    result, [query] = _probe(reply, ["acme/site", "acme/empty"], method="batch_default_branch_refs")

    assert "defaultBranchRef" in query
    assert "refs(" not in query
    site, empty = result.repos
    assert (site.default_branch, [(r.kind, r.name, r.sha) for r in site.refs]) == (
        "trunk",
        [("heads", "trunk", "c1")],
    )
    assert (empty.full_name, empty.default_branch, empty.refs) == ("acme/empty", None, ())
    assert result.rate_limit_cost == 1


@pytest.mark.parametrize(
    ("response", "kind", "message"),
    [
        (httpx.Response(401, json={"message": "Bad credentials"}), "auth", "Bad credentials"),
        (
            httpx.Response(403, json={"message": "API rate limit exceeded for user ID 123."}),
            "rate_limited",
            "API rate limit exceeded",
        ),
        (
            httpx.Response(403, json={"message": "You have exceeded a secondary rate limit."}),
            "secondary_rate_limited",
            "secondary rate limit",
        ),
        (
            httpx.Response(429, json={"message": "You have exceeded a secondary rate limit."}),
            "secondary_rate_limited",
            "secondary rate limit",
        ),
        (
            httpx.Response(
                403, json={"message": "Resource not accessible by personal access token"}
            ),
            "forbidden",
            "Resource not accessible",
        ),
    ],
)
def test_http_access_errors_are_classified_and_never_retried(
    response: httpx.Response, kind: str, message: str
) -> None:
    with respx.mock(base_url="https://api.github.com") as mock:
        route = mock.post("/graphql").mock(side_effect=[response])
        client = GithubClient(GithubSettings(token=SecretStr("ghp_test")))
        with client, pytest.raises(GithubGraphqlError) as exc_info:
            client.batch_repo_refs(["acme/a", "acme/b"])

    assert route.call_count == 1
    assert exc_info.value.kind == kind
    assert message in str(exc_info.value)
    assert exc_info.value.status_code == response.status_code
    assert exc_info.value.body


@pytest.mark.parametrize(
    "body",
    [
        {"message": "Resource not accessible"},
        {"error": "Resource not accessible"},
        {"detail": "Resource not accessible"},
        {"errors": [{"message": "Resource not accessible"}]},
    ],
)
def test_http_error_message_is_extracted_from_common_body_shapes(body: dict[str, object]) -> None:
    error = _raises(httpx.Response(403, json=body), ["acme/a"])

    assert str(error) == "github graphql access forbidden: Resource not accessible"


@pytest.mark.parametrize("bad", ["site", "acme/", "/site", "acme/site/extra", ""])
def test_batch_repo_refs_rejects_invalid_repo_strings(bad: str) -> None:
    # UntapedError, not ValueError: repo strings can come from user source
    # config, and report_errors renders UntapedError as a message.
    with pytest.raises(UntapedError, match="owner/name"):
        _probe([], [bad])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"kinds": ("heads", "releases")}, "releases"),
        ({"kinds": ()}, "kinds must not be empty"),
        ({"chunk_size": 0}, "chunk_size must be positive"),
    ],
)
def test_batch_repo_refs_rejects_invalid_arguments(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _probe([], ["acme/site"], **kwargs)


def test_batch_repo_refs_with_no_repos_makes_no_requests() -> None:
    result, queries = _probe([], [])

    assert queries == []
    assert (result.repos, result.missing, result.rate_limit_remaining) == ((), (), None)
