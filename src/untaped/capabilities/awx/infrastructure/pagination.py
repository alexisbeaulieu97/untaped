"""Auto-pagination for AWX list endpoints.

AWX returns ``{"count", "next", "previous", "results"}`` on every list
endpoint. ``next`` is an absolute path (already including the
configured ``api_prefix``) or ``null`` once exhausted. We follow it
verbatim until exhausted.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from untaped.api import paginate_pages
from untaped.capabilities.awx.infrastructure.awx_client import AwxClient

# `paginate_pages`'s default `max_pages=100` is sized for short cursor
# walks; AWX collections (job events especially) legitimately span far
# more pages, and uncapped iteration was this module's documented
# contract. Keep a generous ceiling so the core helper's
# repeated-cursor/non-convergence guards still apply without capping
# real datasets.
_MAX_PAGES = 10_000


def paginate(
    client: AwxClient,
    path: str,
    *,
    params: dict[str, str] | None = None,
    page_size: int = 200,
    limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield each item from a paginated AWX list endpoint.

    ``path`` is relative to ``api_prefix`` (e.g. ``"job_templates/"``).
    ``params`` are query parameters for the *first* page only — AWX
    bakes them into the ``next`` URL it returns. ``limit`` caps the
    total number of items yielded.

    The cursor loop is core's :func:`untaped.api.paginate_pages`; the
    AWX shape maps onto it with the ``next`` URL as the cursor (``None``
    selects the params-carrying first request).
    """
    initial_params = {**(params or {}), "page_size": str(page_size)}

    def fetch(cursor: str | None) -> tuple[list[dict[str, Any]], str | None]:
        page: dict[str, Any] = (
            client.get_json(path, params=initial_params)
            if cursor is None
            else client.get_absolute_json(cursor)
        )
        return list(page.get("results", [])), page.get("next")

    yield from paginate_pages(fetch, limit=limit, max_pages=_MAX_PAGES)
