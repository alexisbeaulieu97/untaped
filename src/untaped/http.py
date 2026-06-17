"""Thin wrapper around :class:`httpx.Client` with consistent error mapping.

Every HTTP failure (network, timeout, or non-2xx status) is raised as an
:class:`untaped.errors.HttpError`, so domain layers don't need to know
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
from collections.abc import Callable, Iterator, Mapping
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, SecretStr

from untaped.errors import (
    ConfigError,
    HttpError,
    HttpStatusError,
    HttpTransportError,
    UntapedError,
)
from untaped.identity import current_tool_command
from untaped.settings import HttpSettings

if TYPE_CHECKING:
    import httpx

type AuthFn = Callable[[httpx.Request], httpx.Request]
VerifyTypes = bool | str | ssl.SSLContext
PageFetcher = Callable[[str | None], tuple[list[dict[str, Any]], str | None]]


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
    import truststore  # noqa: PLC0415

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
        proxy: str | None = None,
    ) -> None:
        import httpx  # noqa: PLC0415

        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers=headers or {},
            verify=verify,
            proxy=proxy,
        )
        self._auth = auth

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        import httpx  # noqa: PLC0415

        request = self._client.build_request(method, path, **kwargs)
        if self._auth is not None:
            request = self._auth(request)
        try:
            response = self._client.send(request)
        except httpx.HTTPError as exc:
            raise HttpTransportError(str(exc), url=str(request.url)) from exc
        if response.status_code >= 400:
            raise HttpStatusError(
                f"HTTP {response.status_code} for {request.url}",
                status_code=response.status_code,
                url=str(request.url),
                body=_body_snippet(response, _BODY_LIMIT),
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

    def get_json_dict(self, path: str, **kwargs: Any) -> dict[str, Any]:
        """GET ``path`` and assert the JSON body decodes to an object.

        Raises :class:`HttpError` (with full URL + status + body snippet,
        same shape as other ``HttpClient`` errors) when the body is
        anything other than a JSON object (array, scalar, ``null``).
        Adapter sites that promise ``-> dict[str, Any]`` call this
        instead of ``get_json`` so they don't have to suppress
        ``no-any-return`` at the seam.
        """
        response = self.request("GET", path, **kwargs)
        body = _decode_json(response)
        if not isinstance(body, dict):
            raise HttpError(
                f"expected JSON object from {response.request.url}, got {type(body).__name__}",
                status_code=response.status_code,
                url=str(response.request.url),
                body=_body_snippet(response, _BODY_SNIPPET_LIMIT),
            )
        return body

    def get_json_list(self, path: str, **kwargs: Any) -> list[Any]:
        """GET ``path`` and assert the JSON body decodes to an array.

        Raises :class:`HttpError` (with full URL + status + body snippet,
        same shape as other ``HttpClient`` errors) when the body is
        anything other than a JSON array (object, scalar, ``null``).
        """
        response = self.request("GET", path, **kwargs)
        body = _decode_json(response)
        if not isinstance(body, list):
            raise HttpError(
                f"expected JSON array from {response.request.url}, got {type(body).__name__}",
                status_code=response.status_code,
                url=str(response.request.url),
                body=_body_snippet(response, _BODY_SNIPPET_LIMIT),
            )
        return body

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


_BODY_LIMIT = 2048
_BODY_SNIPPET_LIMIT = 256


def _body_snippet(response: httpx.Response, limit: int) -> str:
    """Decode at most ``limit`` bytes of ``response.content``.

    Used at every site that attaches a response body to ``HttpError``:
    the bytes are already buffered by httpx, but the decoded string
    survives on the exception (often through ``report_errors`` to
    stderr) long after the ``Response`` is collected — so the *string*
    needs the cap, not the buffer. ``errors="replace"`` keeps the error
    path crash-free on a truncated multi-byte sequence at the cost of
    a few ``\\ufffd`` characters; charset from the response header is
    intentionally ignored since this only ever feeds diagnostics.
    """
    return response.content[:limit].decode("utf-8", errors="replace")


def _decode_json(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise HttpError(
            f"non-JSON response from {response.request.url}: {exc}",
            status_code=response.status_code,
            url=str(response.request.url),
            body=_body_snippet(response, _BODY_SNIPPET_LIMIT),
        ) from exc


# --------------------------------------------------------------------------- #
# Connection + pagination toolkit for tool API clients
# --------------------------------------------------------------------------- #


def missing_setting_error(section: str, field: str) -> ConfigError:
    """A standard "go set this config key" error for a missing tool setting.

    Names the running tool's command with a bare key (``untaped-github config
    set token``) when a tool is registered; falls back to a neutral
    ``<tool> config set <section>.<field>`` placeholder otherwise (no tool
    registered is a misuse path — e.g. the SDK used without ``run_tool``).
    """
    placeholder = field.rsplit("_", maxsplit=1)[-1]
    command = current_tool_command()
    if command is None:
        cmd, key = "<tool>", f"{section}.{field}"
    else:
        cmd, key = command, field
    return ConfigError(
        f"{section}.{field} is not configured (set it via "
        f"`{cmd} config set {key} <{placeholder}>` or "
        f"UNTAPED_{section.upper()}__{field.upper()})"
    )


def connected_client(
    config: BaseModel,
    *,
    section: str,
    required: tuple[str, ...] = ("base_url", "token"),
    headers: Mapping[str, str] | None = None,
    base_url_field: str = "base_url",
    bearer_token_field: str | None = "token",
    http: HttpSettings | None = None,
) -> HttpClient:
    """Validate a tool's connection settings and build an :class:`HttpClient`.

    Each ``required`` field must be present and non-blank on ``config``
    (:class:`SecretStr` values are unwrapped); a missing one raises the
    standard :func:`missing_setting_error`. ``bearer_token_field`` (when set
    and configured) becomes an ``Authorization: Bearer`` header unless the
    caller supplied their own.
    """
    values: dict[str, str] = {}
    # ``base_url_field`` and ``bearer_token_field`` are always walked — even
    # when not listed in ``required`` — so the client can be built and the
    # bearer header set; de-dup keeps a field named twice from being read twice.
    optional = (base_url_field, bearer_token_field) if bearer_token_field else (base_url_field,)
    for field in dict.fromkeys((*required, *optional)):
        raw = getattr(config, field, None)
        if isinstance(raw, SecretStr):
            raw = raw.get_secret_value()
        value = str(raw).strip() if raw is not None else ""
        if field in required and not value:
            raise missing_setting_error(section, field)
        values[field] = value

    request_headers = dict(headers or {})
    if bearer_token_field is not None:
        token = values.get(bearer_token_field, "")
        if token:
            request_headers.setdefault("Authorization", f"Bearer {token}")

    http_settings = http or HttpSettings()
    return HttpClient(
        base_url=values[base_url_field].rstrip("/"),
        headers=request_headers,
        verify=resolve_verify(http_settings),
        timeout=http_settings.timeout,
        proxy=http_settings.proxy,
    )


def paginate_pages(
    fetch: PageFetcher,
    *,
    limit: int | None,
    max_pages: int = 100,
) -> Iterator[dict[str, Any]]:
    """Drive a cursor-style pagination loop.

    ``fetch`` maps a cursor (``None`` for the first page) to
    ``(items, next_cursor)``; iteration ends when ``next_cursor`` is ``None``,
    repeats (a server echoing the same page forever), or ``limit`` rows have
    been yielded. More than ``max_pages`` fetches raises
    :class:`UntapedError` — a paginator that never converges is a server bug
    worth surfacing, not looping on.
    """
    if limit is not None and limit <= 0:
        return
    cursor: str | None = None
    seen_cursors: set[str] = set()
    yielded = 0
    for _ in range(max_pages):
        items, next_cursor = fetch(cursor)
        for item in items:
            if limit is not None and yielded >= limit:
                return
            yield item
            yielded += 1
        if next_cursor is None or next_cursor in seen_cursors:
            return
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise UntapedError(f"pagination did not converge after {max_pages} pages")


def paginate_offset(
    http: HttpClient,
    method: Literal["GET", "POST"],
    path: str,
    *,
    item_key: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    page_size: int = 50,
    limit: int | None = None,
    start_param: str = "startAt",
    size_param: str = "maxResults",
) -> Iterator[dict[str, Any]]:
    """Walk offset/limit collection envelopes (Jira-style ``startAt`` pages).

    Rows live under ``item_key``; termination honours an ``isLast`` flag or a
    ``total`` count when the server provides one, and otherwise stops at the
    first short or empty page.
    """
    if limit is not None and limit <= 0:
        return
    emitted = 0
    start = 0
    while True:
        request_size = page_size if limit is None else min(page_size, limit - emitted)
        if request_size <= 0:
            return
        window = {start_param: start, size_param: request_size}
        payload = _fetch_offset_page(http, method, path, params=params, body=body, window=window)
        rows = payload.get(item_key) if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            return
        for row in rows:
            if isinstance(row, dict):
                yield row
                emitted += 1
                if limit is not None and emitted >= limit:
                    return
        if _offset_pages_exhausted(payload, start=start, rows=len(rows), requested=request_size):
            return
        start += len(rows)


def _fetch_offset_page(
    http: HttpClient,
    method: Literal["GET", "POST"],
    path: str,
    *,
    params: dict[str, Any] | None,
    body: dict[str, Any] | None,
    window: dict[str, Any],
) -> Any:
    if method == "GET":
        return http.get_json_dict(path, params={**(params or {}), **window})
    return http.request_json("POST", path, json={**(body or {}), **window})


def _offset_pages_exhausted(
    payload: dict[str, Any], *, start: int, rows: int, requested: int
) -> bool:
    if payload.get("isLast") is True:
        return True
    total = payload.get("total")
    if isinstance(total, int) and start + rows >= total:
        return True
    return rows < requested
