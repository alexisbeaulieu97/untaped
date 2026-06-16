"""HTTP toolkit for plugin API clients.

Every HTTP-backed plugin repeats the same setup: validate required settings,
raise a :class:`ConfigError` that tells the user exactly how to fix their
config, build an :class:`HttpClient` with bearer auth and TLS resolution, and
walk a paginated collection. These helpers own those shapes so plugins only
contribute their domain methods.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any, Literal

from pydantic import BaseModel, SecretStr

from untaped.errors import ConfigError, UntapedError
from untaped.http import HttpClient, resolve_verify
from untaped.identity import current_tool_command
from untaped.settings import HttpSettings

PageFetcher = Callable[[str | None], tuple[list[dict[str, Any]], str | None]]


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
    """Validate a plugin's connection settings and build an :class:`HttpClient`.

    Each ``required`` field must be present and non-blank on ``config``
    (:class:`SecretStr` values are unwrapped); a missing one raises the
    standard :func:`missing_setting_error`. ``bearer_token_field`` (when set
    and configured) becomes an ``Authorization: Bearer`` header unless the
    caller supplied their own.
    """
    values: dict[str, str] = {}
    for field in (*required, base_url_field):
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

    return HttpClient(
        base_url=values[base_url_field].rstrip("/"),
        headers=request_headers,
        verify=resolve_verify(http or HttpSettings()),
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
