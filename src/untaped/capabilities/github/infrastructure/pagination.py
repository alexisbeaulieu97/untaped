"""GitHub-flavored wrappers around SDK Link-header pagination."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from untaped.api import HttpClient, paginate_link


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
    return paginate_link(
        http,
        path,
        params=params,
        page_size=per_page,
        limit=limit,
        item_key="items",
    )


def paginate_list(
    http: HttpClient,
    path: str,
    *,
    params: dict[str, str] | None = None,
    per_page: int = 100,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield rows from a non-search list endpoint (raw JSON array body)."""
    return paginate_link(http, path, params=params, page_size=per_page, limit=limit)
