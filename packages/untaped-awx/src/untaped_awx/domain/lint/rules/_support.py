"""What the awx rules share: a finding about an item, and the item a reference names."""

from __future__ import annotations

from typing import Any

from untaped_awx.domain.lint.resources import Linted, Reference, Resources
from untaped_awx.domain.lint.targets import location_of, target_of
from untaped_lint.api import Finding, RuleFunction, finding


def report(
    rule: RuleFunction,
    item: Linted,
    message: str,
    *,
    hint: str | None = None,
    fix: list[str] | None = None,
) -> Finding:
    """A finding of ``rule`` about ``item``: its target and location filled in."""
    return finding(rule, target_of(item), message, location=location_of(item), hint=hint, fix=fix)


def reference(item: Linted, field: str) -> Reference | None:
    """``item``'s reference held in ``field``, if it makes one."""
    return next((each for each in item.references if each.field == field), None)


def referenced(resources: Resources, item: Linted, field: str) -> Linted | None:
    """The item ``item``'s resolved reference in ``field`` names, when the snapshot holds it."""
    found = reference(item, field)
    if found is None or found.resolution != "resolved":
        return None
    return resources.find(found)


def is_set(value: Any) -> bool:
    """Whether a launch field holds a value: not null, an empty string, mapping or list."""
    return value is not None and value != "" and value != {} and value != []


def described(item: Linted) -> str:
    """``Kind 'name'``, as a message names a resource."""
    return f"{item.resource.kind} {item.resource.metadata.name!r}"
