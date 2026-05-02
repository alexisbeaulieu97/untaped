"""Concrete :class:`Catalog` implementation backed by the static spec list."""

from __future__ import annotations

from untaped_awx.domain import ResourceSpec
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.specs import ALL_SPECS


class AwxResourceCatalog:
    """Looks up :class:`ResourceSpec` instances by kind or CLI name.

    The catalog is process-local and immutable — built once from
    :data:`ALL_SPECS` and consulted by the framework's generic use
    cases. Tests can substitute a stub by injecting their own
    :class:`Catalog`-Protocol-shaped object.
    """

    def __init__(self, specs: tuple[ResourceSpec, ...] = ALL_SPECS) -> None:
        self._by_kind: dict[str, ResourceSpec] = {s.kind: s for s in specs}
        self._by_cli_name: dict[str, ResourceSpec] = {s.cli_name: s for s in specs}

    def get(self, kind: str) -> ResourceSpec:
        try:
            return self._by_kind[kind]
        except KeyError as exc:
            raise AwxApiError(
                f"unknown kind {kind!r} (available: {', '.join(sorted(self._by_kind))})"
            ) from exc

    def kinds(self) -> tuple[str, ...]:
        return tuple(self._by_kind)

    def by_cli_name(self, cli_name: str) -> ResourceSpec:
        try:
            return self._by_cli_name[cli_name]
        except KeyError as exc:
            raise AwxApiError(f"unknown CLI name {cli_name!r}") from exc
