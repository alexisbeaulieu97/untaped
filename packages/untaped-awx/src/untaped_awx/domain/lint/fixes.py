"""The ``untaped`` argv that fixes a live finding: a ``patch`` of the resource to change.

A document finding has no ``fix``: its fix is an edit of the file. The kinds
a fix patches are those whose ``patch`` command awx derives once
(:data:`~untaped_awx.commands.resources.PATCH_PROJECT` and its siblings), so
the argv is spelled from the mounted command, under the name the run gives awx.
"""

from __future__ import annotations

import shlex

from untaped.sdk import command_line
from untaped_awx.domain.lint.resources import Linted

PATCHED_KINDS = frozenset({"JobTemplate", "WorkflowJobTemplate", "Project"})
"""The kinds a fix can patch."""


def patch_fix(item: Linted, assignment: str) -> list[str]:
    """``awx <group> patch NAME --organization ORG --set KEY=VALUE``, after ``untaped``.

    An item in no organization is patched by its id (``patch 14 --by-id``):
    without ``--organization`` its name would also select any organization's
    namesake, and ``patch`` refuses the ambiguity.
    """
    # Lazy: the fix spells awx's commands, and the commands import the lint rules.
    from untaped_awx.commands.resources import (  # noqa: PLC0415
        PATCH_JOB_TEMPLATE,
        PATCH_PROJECT,
        PATCH_WORKFLOW_TEMPLATE,
    )

    patch = {
        "JobTemplate": PATCH_JOB_TEMPLATE,
        "WorkflowJobTemplate": PATCH_WORKFLOW_TEMPLATE,
        "Project": PATCH_PROJECT,
    }[item.resource.kind]
    metadata = item.resource.metadata
    if metadata.organization is not None:
        selection = [metadata.name, "--organization", metadata.organization]
    else:
        id_ = str(getattr(item.location, "id", "")).rpartition("/")[2]
        selection = [id_, "--by-id"] if id_.isdigit() else [metadata.name]
    # The words alone are spelled: a name may hold spaces, kept whole as one argument.
    words = shlex.split(command_line(patch.spelled()))[1:]
    return [*words, *selection, "--set", assignment]


__all__ = ["PATCHED_KINDS", "patch_fix"]
