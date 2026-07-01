"""Thin wrapper around :class:`httpx.Client` with consistent error mapping.

Every HTTP failure (network, timeout, or non-2xx status) is raised as an
:class:`untaped.errors.HttpError`, so domain layers don't need to know
about httpx-specific exceptions.

TLS verification is centralised in :func:`resolve_verify`. By default we read
the OS trust store via :mod:`truststore`, which transparently picks up
corporate CAs that have been installed system-wide. Override this with
``http.ca_bundle`` to pin a specific PEM file, ``http.verify_hostname: false``
to keep chain verification but skip the hostname/SAN check (for a self-signed
cert that modern Python rejects on hostname), or ``http.verify_ssl: false`` to
disable verification entirely (escape hatch — leaves traffic open to MITM).
"""

from __future__ import annotations

import email.utils
import ssl
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
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
from untaped.settings import HttpSettings, get_settings

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

type AuthFn = Callable[[httpx.Request], httpx.Request]
VerifyTypes = bool | str | ssl.SSLContext
PageFetcher = Callable[[str | None], tuple[list[dict[str, Any]], str | None]]


@dataclass(frozen=True)
class RetryPolicy:
    """How :class:`HttpClient` retries transient HTTP failures.

    Transport failures are retried by *phase*: a pre-send connect failure never
    reached the server and is safe to retry for any method, while a post-send
    read/write error may already have been processed and is retried only for
    ``idempotent_methods``. Retryable statuses (429/503) are likewise retried
    only for ``idempotent_methods`` — a caller whose POST is genuinely
    idempotent (e.g. a search endpoint) opts in by passing a policy whose
    ``idempotent_methods`` includes ``"POST"``. ``Retry-After`` (delta-seconds
    or HTTP-date) is honoured up to ``retry_after_max``; otherwise the delay is
    exponential backoff capped at ``backoff_max``.
    """

    max_attempts: int = 3
    backoff_base: float = 0.5
    backoff_max: float = 30.0
    retry_after_max: float = 60.0
    retry_statuses: tuple[int, ...] = (429, 503)
    honor_retry_after: bool = True
    retry_on_transport: bool = True
    idempotent_methods: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

    def allows_status_retry(self, method: str, status_code: int) -> bool:
        return status_code in self.retry_statuses and method.upper() in self.idempotent_methods

    def allows_transport_retry(self, method: str, *, presend: bool) -> bool:
        return self.retry_on_transport and (presend or method.upper() in self.idempotent_methods)

    def backoff(self, attempt: int) -> float:
        return min(self.backoff_max, self.backoff_base * 2.0 ** (attempt - 1))

    def status_delay(self, attempt: int, retry_after: str | None) -> float:
        if self.honor_retry_after and retry_after:
            parsed = _parse_retry_after(retry_after)
            if parsed is not None:
                return min(parsed, self.retry_after_max)
        return self.backoff(attempt)


class _Inherit:
    """Sentinel: a per-call ``retry`` left unset inherits the client's policy."""


_INHERIT = _Inherit()
_DEFAULT_RETRY = RetryPolicy()


def _parse_retry_after(value: str) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP-date) to seconds.

    Returns ``None`` when unparseable so the caller falls back to computed
    backoff; a past HTTP-date clamps to ``0`` (retry immediately).
    """
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _sleep(seconds: float) -> None:  # pragma: no cover - thin wrapper, patched in tests
    time.sleep(seconds)


def resolve_verify(http: HttpSettings) -> VerifyTypes:
    """Translate :class:`HttpSettings` into a value httpx accepts for ``verify``.

    Resolution order:

    1. ``verify_ssl`` is ``False`` → ``False`` (no verification at all).
    2. ``verify_hostname`` and ``ca_bundle`` set → the absolute path of that
       file (httpx builds a default, hostname-checking context from it).
    3. Otherwise → an :class:`ssl.SSLContext` whose ``check_hostname`` mirrors
       ``verify_hostname``: the OS trust store (``truststore``) when no
       ``ca_bundle``, else a default context loaded from the bundle.

    ``verify_hostname: false`` keeps chain verification but drops the
    hostname/SAN check — the case where a trusted self-signed cert is still
    rejected by modern Python. The truststore default makes corporate CAs in
    the OS keychain "just work" without per-user configuration.
    """
    if not http.verify_ssl:
        return False
    # Fast path: hostname-checked + a pinned CA → hand httpx the path and let it
    # build the (equivalent) default, hostname-checking context itself.
    if http.verify_hostname and http.ca_bundle is not None:
        return str(http.ca_bundle.expanduser())
    context = _ssl_context(http.ca_bundle)
    context.check_hostname = http.verify_hostname
    return context


def _ssl_context(ca_bundle: Path | None) -> ssl.SSLContext:
    """Build an SSLContext: a bundle-loaded default context, else OS trust."""
    if ca_bundle is not None:
        return ssl.create_default_context(cafile=str(ca_bundle.expanduser()))
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
        retry: RetryPolicy | None = None,
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
        self._retry = retry

    def request(
        self,
        method: str,
        path: str,
        *,
        retry: RetryPolicy | None | _Inherit = _INHERIT,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a request, retrying transient failures per the active policy.

        ``retry`` overrides the client's policy for this call: a
        :class:`RetryPolicy` replaces it, ``None`` disables retries, and the
        default (``_INHERIT``) keeps the client's policy. The request is rebuilt
        each attempt so re-signing auth and re-reading the body stay correct.
        """
        import httpx  # noqa: PLC0415

        policy = self._retry if isinstance(retry, _Inherit) else retry
        attempt = 0
        while True:
            attempt += 1
            request = self._client.build_request(method, path, **kwargs)
            if self._auth is not None:
                request = self._auth(request)
            try:
                response = self._client.send(request)
            except httpx.HTTPError as exc:
                presend = isinstance(
                    exc,
                    httpx.ConnectError
                    | httpx.ConnectTimeout
                    | httpx.PoolTimeout
                    | httpx.ProxyError,
                )
                # Only genuinely transient transport failures are retried; a
                # permanent HTTPError (UnsupportedProtocol, DecodingError,
                # TooManyRedirects, LocalProtocolError) raises immediately
                # rather than burning the backoff budget.
                transient = presend or isinstance(
                    exc,
                    httpx.TimeoutException | httpx.NetworkError | httpx.RemoteProtocolError,
                )
                if (
                    policy is not None
                    and attempt < policy.max_attempts
                    and transient
                    and policy.allows_transport_retry(method, presend=presend)
                ):
                    _sleep(policy.backoff(attempt))
                    continue
                raise HttpTransportError(str(exc), url=str(request.url)) from exc
            if response.status_code >= 400:
                if (
                    policy is not None
                    and attempt < policy.max_attempts
                    and policy.allows_status_retry(method, response.status_code)
                ):
                    _sleep(policy.status_delay(attempt, response.headers.get("Retry-After")))
                    continue
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
        return _decode_json_dict(response)

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


def _decode_json_dict(response: httpx.Response) -> dict[str, Any]:
    body = _decode_json(response)
    if not isinstance(body, dict):
        raise HttpError(
            f"expected JSON object from {response.request.url}, got {type(body).__name__}",
            status_code=response.status_code,
            url=str(response.request.url),
            body=_body_snippet(response, _BODY_SNIPPET_LIMIT),
        )
    return body


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
    retry: RetryPolicy | None = _DEFAULT_RETRY,
) -> HttpClient:
    """Validate a tool's connection settings and build an :class:`HttpClient`.

    Each ``required`` field must be present and non-blank on ``config``
    (:class:`SecretStr` values are unwrapped); a missing one raises the
    standard :func:`missing_setting_error`. ``bearer_token_field`` (when set
    and configured) becomes an ``Authorization: Bearer`` header unless the
    caller supplied their own. ``http`` defaults to the active profile's
    resolved :class:`HttpSettings` (proxy/ca/verify), so per-profile HTTP
    config takes effect without each tool threading it explicitly. ``retry``
    defaults to a safe :class:`RetryPolicy` (transport + idempotent-method
    429/503 backoff); pass ``None`` to disable, or a custom policy to opt a
    POST endpoint in.
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

    http_settings = http if http is not None else get_settings().http
    return HttpClient(
        base_url=values[base_url_field].rstrip("/"),
        headers=request_headers,
        verify=resolve_verify(http_settings),
        timeout=http_settings.timeout,
        proxy=http_settings.proxy,
        retry=retry,
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
    retry: RetryPolicy | None | _Inherit = _INHERIT,
) -> Iterator[dict[str, Any]]:
    """Walk offset/limit collection envelopes (Jira-style ``startAt`` pages).

    Rows live under ``item_key``; termination honours an ``isLast`` flag or a
    ``total`` count when the server provides one, and otherwise stops at the
    first short or empty page. ``retry`` is forwarded per page-fetch: the
    default ``_INHERIT`` uses the client's policy, while a caller fetching an
    idempotent ``POST`` collection (e.g. a JQL search) can pass a
    POST-inclusive :class:`RetryPolicy` to make just that endpoint retry.
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
        payload = _fetch_offset_page(
            http, method, path, params=params, body=body, window=window, retry=retry
        )
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
    retry: RetryPolicy | None | _Inherit = _INHERIT,
) -> Any:
    if method == "GET":
        return http.get_json_dict(path, params={**(params or {}), **window}, retry=retry)
    response = http.request("POST", path, json={**(body or {}), **window}, retry=retry)
    return _decode_json_dict(response)


def _offset_pages_exhausted(
    payload: dict[str, Any], *, start: int, rows: int, requested: int
) -> bool:
    if payload.get("isLast") is True:
        return True
    total = payload.get("total")
    if isinstance(total, int) and start + rows >= total:
        return True
    return rows < requested
