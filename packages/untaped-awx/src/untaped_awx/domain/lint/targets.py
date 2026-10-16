"""How a finding names the resource it is about: its target and its location.

The target is ``Kind/Organization/name`` (``JobTemplate/Platform/deploy-web``),
the organization being the resource's own or its nearest parent's (a
schedule's template's, a host's inventory's), and ``-`` when it has none
(``Organization/-/Default``). The glob ``*/Platform/**`` therefore never
matches an organization-less resource, and ``JobTemplate/**`` matches every
job template whatever its name holds. Ids never appear in a target: they
live in the location (``job_templates/14``), so a document and the
controller name the same resource the same way.
"""

from __future__ import annotations

from untaped.sdk import experimental
from untaped_awx.domain.lint.resources import Linted, organization_of
from untaped_lint.api import DocumentLocation, ResourceLocation

NO_ORGANIZATION = "-"
"""The middle segment of an organization-less resource's target."""


@experimental
def target_of(item: Linted) -> str:
    """``Kind/Organization/name``: what a finding about ``item`` targets."""
    resource = item.resource
    organization = organization_of(item) or NO_ORGANIZATION
    return f"{resource.kind}/{organization}/{resource.metadata.name}"


@experimental
def location_of(item: Linted) -> DocumentLocation | ResourceLocation:
    """Where ``item`` was read: ``templates.yml#2`` or ``job_templates/14``."""
    return item.location


__all__ = ["NO_ORGANIZATION", "location_of", "target_of"]
