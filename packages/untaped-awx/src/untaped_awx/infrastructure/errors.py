"""Map :class:`untaped.HttpError` to typed AWX exceptions.

The mapping uses status codes plus response bodies for actionable
messages, and keeps the source error's attribution (category, system,
``status``/``url`` details). 401 maps to :class:`untaped.ConfigError`
(category ``auth``) so the user sees `awx.token` guidance in their CLI
output instead of an opaque HTTP error. A transport failure stays
``unavailable``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from untaped.sdk import (
    HttpError,
    UntapedError,
    attribution,
    rejected_token_error,
)
from untaped_awx.errors import (
    AwxApiError,
    AwxError,
    BadRequestError,
    ConflictError,
    PermissionDeniedError,
)

_BODY_SNIPPET = 500


def to_awx_error(err: HttpError) -> UntapedError:
    status = err.status_code
    if status == 401:
        return rejected_token_error("awx", "AWX rejected the token (HTTP 401)", cause=err)
    body_msg = _first_field_error(err.body) or (err.body or "")[:_BODY_SNIPPET] or "<empty body>"
    error_type: type[AwxApiError]
    if status == 403:
        error_type, message = PermissionDeniedError, f"permission denied: {body_msg}"
    elif status == 404:
        error_type, message = AwxApiError, f"not found: {err.url}"
    elif status == 409:
        error_type, message = ConflictError, f"conflict: {body_msg}"
    elif status is not None and 400 <= status < 500:
        error_type, message = BadRequestError, f"HTTP {status}: {body_msg}"
    elif status is not None and status >= 500:
        error_type, message = AwxApiError, f"AWX server error (HTTP {status}): {body_msg}"
    else:
        error_type, message = AwxApiError, str(err) or "AWX request failed"
    return error_type(message, status=status, body=err.body, url=err.url, **attribution(err))


@contextmanager
def map_awx_errors() -> Iterator[None]:
    """Wrap calls into AwxClient so HTTP failures surface as typed exceptions.

    An error that is already an :class:`AwxError` (an inner block mapped it)
    passes through unchanged.
    """
    try:
        yield
    except AwxError:
        raise
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
    except ValueError, TypeError:
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
