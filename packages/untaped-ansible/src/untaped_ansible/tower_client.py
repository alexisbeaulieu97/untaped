from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import requests


class TowerClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> requests.Response:
        return requests.get(
            f"{self._base_url}{path}",
            headers=self._headers,
            params=params,
            timeout=self._timeout,
        )

    def post(self, path: str, json: Mapping[str, Any] | None = None) -> requests.Response:
        return requests.post(
            f"{self._base_url}{path}",
            headers=self._headers,
            json=json,
            timeout=self._timeout,
        )

    def patch(self, path: str, json: Mapping[str, Any] | None = None) -> requests.Response:
        return requests.patch(
            f"{self._base_url}{path}",
            headers=self._headers,
            json=json,
            timeout=self._timeout,
        )

    def delete(self, path: str) -> requests.Response:
        return requests.delete(
            f"{self._base_url}{path}",
            headers=self._headers,
            timeout=self._timeout,
        )
