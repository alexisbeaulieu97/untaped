from __future__ import annotations

from typing import Any, Mapping, Optional

import httpx


class TowerClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def get(self, path: str, params: Optional[Mapping[str, Any]] = None) -> httpx.Response:
        return self._client.get(path, params=params)

    def post(self, path: str, json: Optional[Mapping[str, Any]] = None) -> httpx.Response:
        return self._client.post(path, json=json)

    def patch(self, path: str, json: Optional[Mapping[str, Any]] = None) -> httpx.Response:
        return self._client.patch(path, json=json)

    def delete(self, path: str) -> httpx.Response:
        return self._client.delete(path)

    def close(self) -> None:
        self._client.close()


