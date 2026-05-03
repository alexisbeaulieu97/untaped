"""Typed wrappers at the AWX use-case boundary.

Three thin Pydantic models replace ``dict[str, Any]`` at the Protocols
declared in :mod:`untaped_awx.application.ports`:

- :class:`ServerRecord` — what AWX returns. Always has ``id``; ``name``
  is the canonical identity field for most kinds. Other fields are kept
  via ``extra="allow"`` so callers can read them as attributes
  (``record.organization``) or via :meth:`get`.
- :class:`WritePayload` — what we send on create/update. Field-shape is
  per-kind, so ``extra="allow"`` covers everything; no fields are
  required by the wrapper itself.
- :class:`ActionPayload` — what we send to a custom action endpoint
  (e.g. ``launch``, ``project update``). Same shape as ``WritePayload``;
  separate type so signatures document intent.

The wrappers are frozen — once constructed (by an adapter on read or by
a use case on write) they are immutable.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ServerRecord(BaseModel):
    """A record returned by AWX (post-deserialization).

    The dict-style access methods below reach into ``__pydantic_extra__``
    — Pydantic v2's documented attribute for fields stored under
    ``extra="allow"``. It's stable across the v2 line; if Pydantic v3
    renames it, update here and re-run ``test_server_record_dict_access``.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    id: int
    name: str | None = None

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style access for backward compatibility with raw payloads.

        Matches Python ``dict.get`` semantics: ``default`` is returned
        only when ``key`` is *absent*, not when it's present-but-``None``.
        Concretely, ``record.get("name", "x")`` returns ``"x"`` only when
        AWX omitted ``name`` entirely AND the field isn't declared on
        ``ServerRecord``. Since ``name`` IS declared (as
        ``str | None = None``), a missing AWX response yields
        ``self.name == None`` and ``record.get("name", "x")`` returns
        ``None``. Callers that need ``""`` for a missing name should
        write ``record.get("name") or ""`` rather than relying on the
        default.
        """
        if key in self.__class__.model_fields:
            return getattr(self, key)
        if self.__pydantic_extra__ is None:
            return default
        return self.__pydantic_extra__.get(key, default)

    def __getitem__(self, key: str) -> Any:
        """Dict-style access: ``record["organization"]``."""
        if key in self.__class__.model_fields:
            return getattr(self, key)
        if self.__pydantic_extra__ is None or key not in self.__pydantic_extra__:
            raise KeyError(key)
        return self.__pydantic_extra__[key]

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        if key in self.__class__.model_fields:
            return True
        return self.__pydantic_extra__ is not None and key in self.__pydantic_extra__


class WritePayload(BaseModel):
    """The body of a create/update request to AWX."""

    model_config = ConfigDict(extra="allow", frozen=True)


class ActionPayload(BaseModel):
    """The body of a custom-action POST (e.g. ``launch``, ``update``)."""

    model_config = ConfigDict(extra="allow", frozen=True)
