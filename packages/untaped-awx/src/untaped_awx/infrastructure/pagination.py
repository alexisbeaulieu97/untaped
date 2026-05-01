"""Auto-pagination for AWX list endpoints.

AWX returns ``{"count", "next", "previous", "results"}`` on every list
endpoint. ``next`` is an absolute path (already including the
configured ``api_prefix``) or ``null`` once exhausted. We follow it
verbatim until exhausted.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from untaped_awx.infrastructure.awx_client import AwxClient


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
    """
    initial_params = {**(params or {}), "page_size": str(page_size)}
    page: dict[str, Any] = client.get_json(path, params=initial_params)
    yielded = 0
    while True:
        for item in page.get("results", []):
            if limit is not None and yielded >= limit:
                return
            yield item
            yielded += 1
        next_url = page.get("next")
        if not next_url:
            return
        page = client.get_absolute_json(next_url)
