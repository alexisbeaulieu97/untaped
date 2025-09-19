from __future__ import annotations

from typing import Any

import httpx


class TowerApiError(Exception):
    """Base exception raised for Tower API failures."""

    def __init__(self, message: str, *, response: httpx.Response | None = None, payload: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.response = response
        self.status_code = response.status_code if response is not None else None
        self.payload = payload

    def __str__(self) -> str:  # pragma: no cover - trivial
        status = f" (status {self.status_code})" if self.status_code is not None else ""
        return f"{self.message}{status}"


class TowerAuthenticationError(TowerApiError):
    """Raised when authentication with Tower fails (HTTP 401)."""
