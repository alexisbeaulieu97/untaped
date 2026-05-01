"""Map :class:`untaped_core.HttpError` to typed AWX exceptions.

The mapping uses status codes plus response bodies for actionable
messages. 401 maps to :class:`untaped_core.ConfigError` so the user
sees `awx.token` guidance in their CLI output instead of an opaque
HTTP error.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from untaped_core import ConfigError, HttpError, UntapedError

from untaped_awx.errors import (
    AwxApiError,
    BadRequest,
    Conflict,
    PermissionDenied,
)

_BODY_SNIPPET = 500


def to_awx_error(err: HttpError) -> UntapedError:
    status = err.status_code
    snippet = (err.body or "")[:_BODY_SNIPPET]
    body_msg = _first_field_error(err.body) or snippet or "<empty body>"

    if status == 401:
        return ConfigError(
            "AWX rejected the token (HTTP 401); update via "
            "`untaped config set awx.token <new-token>`"
        )
    if status == 403:
        return PermissionDenied(
            f"permission denied: {body_msg}",
            status=status,
            body=err.body,
            url=err.url,
        )
    if status == 404:
        return AwxApiError(
            f"not found: {err.url}",
            status=status,
            body=err.body,
            url=err.url,
        )
    if status == 409:
        return Conflict(
            f"conflict: {body_msg}",
            status=status,
            body=err.body,
            url=err.url,
        )
    if status is not None and 400 <= status < 500:
        return BadRequest(
            f"HTTP {status}: {body_msg}",
            status=status,
            body=err.body,
            url=err.url,
        )
    if status is not None and status >= 500:
        return AwxApiError(
            f"AWX server error (HTTP {status}): {body_msg}",
            status=status,
            body=err.body,
            url=err.url,
        )
    return AwxApiError(
        str(err) or "AWX request failed",
        status=status,
        body=err.body,
        url=err.url,
    )


@contextmanager
def map_awx_errors() -> Iterator[None]:
    """Wrap calls into AwxClient so HTTP failures surface as typed exceptions."""
    try:
        yield
    except HttpError as exc:
        raise to_awx_error(exc) from exc


def _first_field_error(body: str | None) -> str | None:
    """Pull a usable message out of an AWX error response body.

    AWX validation errors come back as JSON with field-keyed lists:
    ``{"name": ["Already exists"]}``. We surface the first one for a
    crisp error message.
    """
    if not body:
        return None
    data: Any
    try:
        data = json.loads(body)
    except ValueError:
        return None
    except TypeError:
        return None
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) and value:
                return f"{key}: {value[0]}"
            if isinstance(value, str):
                return f"{key}: {value}"
    if isinstance(data, str):
        return data
    return None
