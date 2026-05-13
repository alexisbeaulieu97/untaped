"""Link-header pagination for GitHub's REST API.

GitHub paginates list and search endpoints with an ``RFC 5988 Link``
response header (``Link: <url>; rel="next", <url>; rel="last"``). The
``next`` URL is absolute and carries every cursor parameter the server
needs to resume; we follow it verbatim until exhausted or the caller's
``limit`` is reached.

Search responses wrap rows under ``items``; list responses return a raw
array. The two helpers below cover both shapes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from untaped_core import HttpClient, UntapedError

_NEXT_LINK = re.compile(r'<(?P<url>[^>]+)>;\s*rel="next"')

# GitHub's search ceiling is 1000 rows ÷ 100 per_page = 10 pages. List
# endpoints are unbounded server-side but in practice paginate finitely.
# A misbehaving server / proxy returning a self-referential ``next`` is
# the only realistic way to spin here; cap defensively.
_MAX_PAGES = 100


def _parse_next(link_header: str | None) -> str | None:
    if not link_header:
        return None
    match = _NEXT_LINK.search(link_header)
    return match.group("url") if match else None


def _paginate(
    http: HttpClient,
    path: str,
    *,
    params: dict[str, str],
    per_page: int,
    limit: int | None,
    extract: str | None,
) -> Iterator[dict[str, Any]]:
    first_page = min(per_page, limit) if limit is not None else per_page
    request_params: dict[str, str] | None = {**params, "per_page": str(first_page)}
    url: str = path
    yielded = 0
    visited: set[str] = set()
    for _ in range(_MAX_PAGES):
        response = http.get(url, params=request_params)
        payload = response.json()
        items = payload[extract] if extract is not None else payload
        if not isinstance(items, list):
            return
        for item in items:
            if limit is not None and yielded >= limit:
                return
            yield item
            yielded += 1
        next_url = _parse_next(response.headers.get("link"))
        if not next_url or next_url == url or next_url in visited:
            return
        visited.add(url)
        url = next_url
        request_params = None
    raise UntapedError(
        f"github pagination did not converge after {_MAX_PAGES} pages — "
        f"suspect a malformed Link header from {path}",
    )


def paginate_search(
    http: HttpClient,
    path: str,
    *,
    params: dict[str, str],
    per_page: int = 100,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield rows from a GitHub ``/search/*`` endpoint.

    Search payloads nest results under ``items``; total counts and the
    ``incomplete_results`` flag are ignored — callers that need them
    can issue the request manually.
    """
    return _paginate(http, path, params=params, per_page=per_page, limit=limit, extract="items")


def paginate_list(
    http: HttpClient,
    path: str,
    *,
    params: dict[str, str] | None = None,
    per_page: int = 100,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield rows from a non-search list endpoint (raw JSON array body)."""
    return _paginate(
        http, path, params=dict(params or {}), per_page=per_page, limit=limit, extract=None
    )
