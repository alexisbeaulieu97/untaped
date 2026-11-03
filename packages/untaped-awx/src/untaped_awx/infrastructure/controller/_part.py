"""What every part of the controller's adapter shares: its client, and calls through it."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from untaped.sdk import HttpError
from untaped_awx.errors import ResourceNotFoundError
from untaped_awx.infrastructure.errors import map_awx_errors
from untaped_awx.infrastructure.pagination import paginate

if TYPE_CHECKING:
    from untaped_awx.infrastructure.awx_client import AwxClient


class ControllerPart(ABC):
    """A part of :class:`~untaped_awx.infrastructure.controller.ControllerV2`.

    Each part builds the paths of its area; the adapter supplies the client
    (under its version's prefix) and the page size. Paths are relative to
    the prefix. A call to a collection every served version has says so
    (``collection=True``), so a 404 there is a mismatch, while a 404 on a
    record is the record gone.
    """

    #: Results requested per list page (``awx.page_size``).
    _page_size: int

    @property
    @abstractmethod
    def _client(self) -> AwxClient:
        """The controller client every request goes through."""

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: dict[str, Any] | None = None,
        collection: bool = False,
    ) -> dict[str, Any]:
        """One request, its JSON answer; ``collection``: ``path`` is a collection."""
        with map_awx_errors():
            return self._client.request_json(  # type: ignore[no-any-return]
                method, path, params=params, json=json, collection=collection
            )

    def _record(self, path: str, *, kind: str, id_: int) -> dict[str, Any]:
        """The ``kind`` record ``id_`` at ``path``; :class:`ResourceNotFoundError` if gone."""
        with map_awx_errors():
            try:
                return self._client.get_json(path)  # type: ignore[no-any-return]
            except HttpError as exc:
                if exc.status_code == 404:
                    raise ResourceNotFoundError(kind, {"id": id_}, url=exc.url) from exc
                raise

    def _paginate(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        limit: int | None = None,
        collection: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Every item of the list at ``path``, page after page (``limit`` caps them).

        ``collection``: ``path`` is a collection, so a 404 on it is a mismatch.
        """
        with map_awx_errors():
            yield from paginate(
                self._client,
                path,
                params=dict(params or {}),
                page_size=self._page_size,
                limit=limit,
                collection=collection,
            )

    def _text(self, path: str, *, params: Mapping[str, str] | None = None) -> str:
        """``GET path``'s text answer."""
        with map_awx_errors():
            return self._client.request_text("GET", path, params=params)


__all__ = ["ControllerPart"]
