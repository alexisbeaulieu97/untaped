"""HTTP client for the AAP / AWX REST API.

URL composition flows through :meth:`AwxClient._url` so every call respects
the configured ``api_prefix`` (default ``/api/controller/v2/`` for AAP;
upstream AWX users set it to ``/api/v2/``).
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

from untaped_core import ConfigError, HttpClient, HttpSettings
from untaped_core.http import resolve_verify

from untaped_awx.infrastructure.config import AwxConfig


class AwxClient:
    """Talks to AAP/AWX REST endpoints using the configured token."""

    def __init__(self, config: AwxConfig, *, http: HttpSettings | None = None) -> None:
        if not config.base_url:
            raise ConfigError(
                "awx.base_url is not configured (set it via "
                "`untaped config set awx.base_url <url>` or UNTAPED_AWX__BASE_URL)"
            )
        headers: dict[str, str] = {"Accept": "application/json"}
        if config.token is not None:
            headers["Authorization"] = f"Bearer {config.token.get_secret_value()}"
        self._http = HttpClient(
            base_url=config.base_url.rstrip("/"),
            headers=headers,
            verify=resolve_verify(http or HttpSettings()),
        )
        self._api_prefix = config.api_prefix

    def _url(self, path: str) -> str:
        """Join ``api_prefix`` with a relative resource path.

        ``path`` must NOT start with ``/`` — it's a path under the prefix
        (e.g. ``ping/``, ``job_templates/42/``).
        """
        return f"{self._api_prefix}{path.lstrip('/')}"

    def ping(self) -> dict[str, Any]:
        return self._http.get_json(self._url("ping/"))  # type: ignore[no-any-return]

    def get_json(self, path: str, **kwargs: Any) -> Any:
        """GET ``<api_prefix><path>`` and return the JSON body."""
        return self._http.get_json(self._url(path), **kwargs)

    def post_json(self, path: str, **kwargs: Any) -> Any:
        """POST ``<api_prefix><path>`` and return the JSON body."""
        return self._http.post_json(self._url(path), **kwargs)

    def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        """Generic verb under ``<api_prefix>``. Returns the JSON body
        or ``None`` for empty 204 responses (e.g. DELETE)."""
        return self._http.request_json(method, self._url(path), **kwargs)

    def request_text(self, method: str, path: str, **kwargs: Any) -> str:
        """Generic verb under ``<api_prefix>``; returns the raw response body
        as text (no JSON decode). Use for endpoints like ``jobs/<id>/stdout/``."""
        response = self._http.request(method, self._url(path), **kwargs)
        return response.text

    def get_absolute_json(self, absolute_path: str, **kwargs: Any) -> Any:
        """GET an absolute server path (already includes ``api_prefix``).

        Used to follow AWX's pagination ``next`` URLs, which come back
        as full paths and would double-prefix if passed through
        :meth:`_url`.
        """
        return self._http.get_json(absolute_path, **kwargs)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AwxClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
