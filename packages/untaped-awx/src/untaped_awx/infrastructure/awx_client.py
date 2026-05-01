"""HTTP client for the AAP / AWX REST API."""

from __future__ import annotations

from types import TracebackType
from typing import Any

from untaped_core import ConfigError, HttpClient, Settings, get_settings
from untaped_core.http import resolve_verify


class AwxClient:
    """Talks to AAP's ``/api/v2/...`` endpoints using the configured token."""

    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        if not s.awx.base_url:
            raise ConfigError(
                "awx.base_url is not configured (set it via "
                "`untaped config set awx.base_url <url>` or UNTAPED_AWX__BASE_URL)"
            )
        headers: dict[str, str] = {"Accept": "application/json"}
        if s.awx.token is not None:
            headers["Authorization"] = f"Bearer {s.awx.token.get_secret_value()}"
        self._http = HttpClient(
            base_url=s.awx.base_url.rstrip("/"),
            headers=headers,
            verify=resolve_verify(s.http),
        )

    def ping(self) -> dict[str, Any]:
        return self._http.get("/api/v2/ping/").json()  # type: ignore[no-any-return]

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
