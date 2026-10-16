"""The ``untaped`` argv that fixes a live finding: a ``patch`` of the resource to change.

A ``fix`` names a command group by its CLI name, which only the catalog
(infrastructure) carries, so the few kinds a fix patches are listed here and
a test pins them to the catalog. A document finding has no ``fix``: its fix
is an edit of the file.
"""

from __future__ import annotations

PATCH_GROUPS: dict[str, str] = {
    "JobTemplate": "job-templates",
    "WorkflowJobTemplate": "workflow-templates",
    "Project": "projects",
}
"""Kind → the ``untaped awx`` group whose ``patch`` sets one of its fields."""


def patch_fix(kind: str, name: str, organization: str | None, assignment: str) -> list[str]:
    """``awx <group> patch NAME [--organization ORG] --set KEY=VALUE``, after ``untaped``."""
    argv = ["awx", PATCH_GROUPS[kind], "patch", name]
    if organization is not None:
        argv += ["--organization", organization]
    return [*argv, "--set", assignment]


__all__ = ["PATCH_GROUPS", "patch_fix"]
