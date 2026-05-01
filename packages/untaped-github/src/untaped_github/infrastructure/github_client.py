"""HTTP client for the GitHub REST API."""

from __future__ import annotations

from types import TracebackType
from typing import Any

from untaped_core import ConfigError, HttpClient, Settings, get_settings
from untaped_core.http import resolve_verify


class GithubClient:
    """Talks to ``api.github.com`` (or a GHE base) using the configured token."""

    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        if s.github.token is None:
            raise ConfigError(
                "github.token is not configured (set it via "
                "`untaped config set github.token <token>` or UNTAPED_GITHUB__TOKEN)"
            )
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {s.github.token.get_secret_value()}",
        }
        self._http = HttpClient(
            base_url=s.github.base_url.rstrip("/"),
            headers=headers,
            verify=resolve_verify(s.http),
        )

    def me(self) -> dict[str, Any]:
        return self._http.get("/user").json()  # type: ignore[no-any-return]

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GithubClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
