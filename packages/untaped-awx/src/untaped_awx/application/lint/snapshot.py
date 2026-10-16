"""What every snapshot carries whatever its origin: the catalog's facts per kind."""

from __future__ import annotations

from untaped_awx.application.resources.ports import Catalog
from untaped_awx.domain.lint.resources import KindFacts


def kind_facts(catalog: Catalog) -> dict[str, KindFacts]:
    """Each catalog kind's known fields and secret paths, by kind."""
    facts: dict[str, KindFacts] = {}
    for kind in catalog.kinds():
        spec = catalog.get(kind)
        facts[kind] = KindFacts(
            known_fields=tuple(sorted(spec.known_fields)), secret_paths=spec.secret_paths
        )
    return facts


__all__ = ["kind_facts"]
