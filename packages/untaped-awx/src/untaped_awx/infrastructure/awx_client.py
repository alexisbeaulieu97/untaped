"""HTTP client for the AAP / AWX REST API, under one controller API prefix.

:class:`~untaped_awx.infrastructure.controller.ControllerV2` builds one per
connection: URL composition flows through :meth:`AwxClient._url`, so every call
lands under the prefix the controller's version calls for (``/api/v2/``, or
``/api/controller/v2/`` behind AAP's platform gateway). Every request runs
inside :meth:`ApiMismatch.watch <untaped.sdk.network.ApiMismatch.watch>`: a
404 or 405 without AWX's own ``detail`` body, or a 2xx answer that doesn't
read, says the server no longer answers as this API does. So does any 404 on
a collection (``collection=True``): a served version always has its
collections, so only a moved path answers one that way.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from untaped.sdk.network import ApiMismatch, same_origin
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.errors import map_awx_errors

if TYPE_CHECKING:
    from untaped.sdk import HttpStatusError
    from untaped.sdk.network import HttpClient


def awx_error(error: HttpStatusError) -> bool:
    """Whether a 404 or 405 is AWX's own answer: a JSON body with its ``detail``.

    AWX answers a missing record (``{"detail": "Not found."}``) and a refused
    method that way; anything else (a proxy's page, an empty body) is a path
    this server doesn't have.
    """
    try:
        body = json.loads(error.body or "")
    except ValueError:
        return False
    return isinstance(body, dict) and isinstance(body.get("detail"), str)


def _collection_error(error: HttpStatusError) -> bool:
    """Whether an answer on a collection is AWX's own: a 405 that :func:`awx_error` recognises.

    A 404 never is: a collection is there on every version that serves it.
    """
    return error.status_code == 405 and awx_error(error)


class AwxClient:
    """Talks to AAP/AWX REST endpoints under ``prefix`` through one ``http`` client."""

    def __init__(self, http: HttpClient, *, prefix: str, base_url: str) -> None:
        self._http = http
        self._prefix = prefix
        self._base_url = base_url

    @staticmethod
    @contextmanager
    def _watched(*, collection: bool = False) -> Iterator[None]:
        with ApiMismatch.watch(vendor_error=_collection_error if collection else awx_error):
            yield

    def _url(self, path: str) -> str:
        """Join the prefix with a relative resource path.

        ``path`` must NOT start with ``/`` — it's a path under the prefix
        (e.g. ``ping/``, ``job_templates/42/``).
        """
        return f"{self._prefix}{path.lstrip('/')}"

    def ping(self) -> dict[str, Any]:
        with self._watched():
            return self._http.get_json_dict(self._url("ping/"))

    def me(self) -> dict[str, Any]:
        """``GET me/``: the authenticated user (401 maps to ConfigError)."""
        with map_awx_errors(), self._watched():
            return self._http.get_json_dict(self._url("me/"))

    def get_json(self, path: str, *, collection: bool = False, **kwargs: Any) -> Any:
        """GET ``<prefix><path>`` and return the JSON body."""
        with self._watched(collection=collection):
            return self._http.get_json(self._url(path), **kwargs)

    def post_json(self, path: str, *, collection: bool = False, **kwargs: Any) -> Any:
        """POST ``<prefix><path>`` and return the JSON body."""
        with self._watched(collection=collection):
            return self._http.post_json(self._url(path), **kwargs)

    def request_json(
        self, method: str, path: str, *, collection: bool = False, **kwargs: Any
    ) -> Any:
        """Generic verb under the prefix. Returns the JSON body
        or ``None`` for empty 204 responses (e.g. DELETE)."""
        with self._watched(collection=collection):
            return self._http.request_json(method, self._url(path), **kwargs)

    def delete(self, path: str) -> int:
        """Keep the HTTP status so callers do not confuse 202 with removal."""
        with self._watched():
            return self._http.request("DELETE", self._url(path)).status_code

    def request_text(self, method: str, path: str, **kwargs: Any) -> str:
        """Generic verb under the prefix; returns the raw response body
        as text (no JSON decode). Use for endpoints like ``jobs/<id>/stdout/``.

        The adapter's connection pins ``Accept: application/json`` so JSON
        endpoints negotiate cleanly. Text endpoints
        like ``jobs/<id>/stdout/`` return ``text/plain`` and reject the
        JSON Accept with HTTP 406 — override per-call here so callers
        don't have to know. Any caller-supplied ``headers`` still win.
        """
        headers = {"Accept": "text/plain", **(kwargs.pop("headers", None) or {})}
        with self._watched():
            return self._http.request(method, self._url(path), headers=headers, **kwargs).text

    def get_absolute_json(self, absolute_path: str, **kwargs: Any) -> Any:
        """GET an absolute server path (already including the prefix).

        Used to follow AWX's pagination ``next`` URLs, which come back
        as full paths and would double-prefix if passed through
        :meth:`_url`. A URL naming another scheme, host or port than the
        configured ``awx.server.url`` is refused, so the token never leaves it.
        """
        if not same_origin(absolute_path, self._base_url):
            raise AwxApiError(
                f"refusing to follow {absolute_path!r}: its origin differs from "
                f"the configured awx.server.url {self._base_url!r}"
            )
        with self._watched():
            return self._http.get_json(absolute_path, **kwargs)
