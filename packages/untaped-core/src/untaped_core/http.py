"""Thin wrapper around :class:`httpx.Client` with consistent error mapping.

Every HTTP failure (network, timeout, or non-2xx status) is raised as an
:class:`untaped_core.errors.HttpError`, so domain layers don't need to know
about httpx-specific exceptions.

TLS verification is centralised in :func:`resolve_verify`. By default we read
the OS trust store via :mod:`truststore`, which transparently picks up
corporate CAs that have been installed system-wide. Override this with
``http.ca_bundle`` in settings to pin a specific PEM file, or
``http.verify_ssl: false`` to disable verification (escape hatch — leaves
traffic open to MITM).
"""

from __future__ import annotations

import ssl
from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING, Any

import httpx
import truststore

from untaped_core.errors import HttpError

if TYPE_CHECKING:
    from untaped_core.settings import HttpSettings

AuthFn = Callable[[httpx.Request], httpx.Request]
VerifyTypes = bool | str | ssl.SSLContext


def resolve_verify(http: HttpSettings) -> VerifyTypes:
    """Translate :class:`HttpSettings` into a value httpx accepts for ``verify``.

    Resolution order:

    1. If ``verify_ssl`` is ``False`` → return ``False`` (no verification).
    2. If ``ca_bundle`` is set → return the absolute path of that file.
    3. Otherwise → return an SSLContext backed by the OS trust store
       (``truststore``).

    The truststore default makes corporate CAs that are installed in the
    OS keychain (macOS Keychain, Windows certstore, Linux system trust)
    "just work" without per-user configuration.
    """
    if not http.verify_ssl:
        return False
    if http.ca_bundle is not None:
        return str(http.ca_bundle.expanduser())
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class HttpClient:
    """A minimal HTTP client suitable for talking to JSON APIs."""

    def __init__(
        self,
        base_url: str = "",
        *,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        auth: AuthFn | None = None,
        verify: VerifyTypes = True,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers=headers or {},
            verify=verify,
        )
        self._auth = auth

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        request = self._client.build_request(method, path, **kwargs)
        if self._auth is not None:
            request = self._auth(request)
        try:
            response = self._client.send(request)
        except httpx.HTTPError as exc:
            raise HttpError(str(exc), url=str(request.url)) from exc
        if response.status_code >= 400:
            raise HttpError(
                f"HTTP {response.status_code} for {request.url}",
                status_code=response.status_code,
                url=str(request.url),
                body=response.text,
            )
        return response

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        """Decode the JSON body, mapping parse failures to :class:`HttpError`.

        Returns ``None`` for empty bodies (e.g. 204 DELETE).
        """
        response = self.request(method, path, **kwargs)
        return _decode_json(response)

    def get_json(self, path: str, **kwargs: Any) -> Any:
        return self.request_json("GET", path, **kwargs)

    def post_json(self, path: str, **kwargs: Any) -> Any:
        return self.request_json("POST", path, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


_BODY_SNIPPET_LIMIT = 256


def _decode_json(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        # Slice bytes before decoding so a multi-MB error page doesn't
        # decode the whole body just to keep the first 256 chars.
        snippet = response.content[:_BODY_SNIPPET_LIMIT].decode("utf-8", errors="replace")
        raise HttpError(
            f"non-JSON response from {response.request.url}: {exc}",
            status_code=response.status_code,
            url=str(response.request.url),
            body=snippet,
        ) from exc
