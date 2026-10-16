"""The ``untaped`` argv that fixes a live finding: a ``patch`` of the resource to change.

A ``fix`` names a command group by its CLI name, which only the catalog
(infrastructure) carries, so the few kinds a fix patches are listed here and
a test pins them to the catalog. A document finding has no ``fix``: its fix
is an edit of the file.
"""

from __future__ import annotations

from untaped_awx.domain.lint.resources import Linted

PATCH_GROUPS: dict[str, str] = {
    "JobTemplate": "job-templates",
    "WorkflowJobTemplate": "workflow-templates",
    "Project": "projects",
}
"""Kind → the ``untaped awx`` group whose ``patch`` sets one of its fields."""


def patch_fix(item: Linted, assignment: str) -> list[str]:
    """``awx <group> patch NAME --organization ORG --set KEY=VALUE``, after ``untaped``.

    An item in no organization is patched by its id (``patch 14 --by-id``):
    without ``--organization`` its name would also select any organization's
    namesake, and ``patch`` refuses the ambiguity.
    """
    kind, metadata = item.resource.kind, item.resource.metadata
    group = PATCH_GROUPS[kind]
    if metadata.organization is not None:
        selection = [metadata.name, "--organization", metadata.organization]
    else:
        id_ = str(getattr(item.location, "id", "")).rpartition("/")[2]
        selection = [id_, "--by-id"] if id_.isdigit() else [metadata.name]
    return ["awx", group, "patch", *selection, "--set", assignment]


__all__ = ["PATCH_GROUPS", "patch_fix"]
