from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Mapping

import httpx

from .errors import TowerApiError, TowerAuthenticationError


class TowerApiClient:
    """Thin wrapper around :class:`httpx.Client` with Tower defaults."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        verify: bool | str | None = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
            verify=verify,
        )

    @property
    def token(self) -> str | None:
        return self._token

    def set_token(self, token: str | None) -> None:
        self._token = token

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TowerApiClient":  # pragma: no cover - rarely exercised
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover - rarely exercised
        self.close()

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        expected_status: int | Iterable[int] | None = 200,
    ) -> httpx.Response:
        return self._request("GET", path, params=params, expected_status=expected_status)

    def post(
        self,
        path: str,
        *,
        json: Any | None = None,
        data: Any | None = None,
        expected_status: int | Iterable[int] | None = 200,
        use_auth: bool = True,
    ) -> httpx.Response:
        return self._request(
            "POST",
            path,
            json=json,
            data=data,
            expected_status=expected_status,
            use_auth=use_auth,
        )

    def patch(
        self,
        path: str,
        *,
        json: Any | None = None,
        expected_status: int | Iterable[int] | None = 200,
    ) -> httpx.Response:
        return self._request("PATCH", path, json=json, expected_status=expected_status)

    def delete(
        self,
        path: str,
        *,
        expected_status: int | Iterable[int] | None = (200, 202, 204),
    ) -> httpx.Response:
        return self._request("DELETE", path, expected_status=expected_status)

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected_status: int | Iterable[int] | None,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        data: Any | None = None,
        headers: Mapping[str, str] | None = None,
        use_auth: bool = True,
    ) -> httpx.Response:
        request_headers = {"Accept": "application/json"}
        if use_auth and self._token:
            request_headers["Authorization"] = f"Token {self._token}"
        if headers:
            request_headers.update(headers)

        response = self._client.request(
            method,
            path,
            headers=request_headers,
            params=params,
            json=json,
            data=data,
        )

        if expected_status is not None:
            allowed = {expected_status} if isinstance(expected_status, int) else set(expected_status)
            if response.status_code not in allowed:
                self._raise_api_error(response)

        return response

    def _raise_api_error(self, response: httpx.Response) -> None:
        payload: Any | None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                message = payload.get("detail") or payload.get("message") or response.text
            else:
                message = str(payload)
        except ValueError:  # pragma: no cover - non-JSON
            payload = None
            message = response.text or f"HTTP {response.status_code}"

        if response.status_code == 401:
            raise TowerAuthenticationError(message, response=response, payload=payload)

        raise TowerApiError(message, response=response, payload=payload)
