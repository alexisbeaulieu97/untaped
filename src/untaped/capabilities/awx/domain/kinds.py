"""Spellings of AWX resource kinds: PascalCase kinds, AWX ``type`` strings, pipe hints.

One home for the transforms between a PascalCase kind (``JobTemplate``), the
snake_case ``type`` AWX reports (``job_template``), and the ``--format pipe``
kind hint (``awx.job_template``), plus the kinds behind AWX's polymorphic
``unified_job_templates`` view.
"""

from __future__ import annotations

import re

UNIFIED_TEMPLATE_KINDS: tuple[str, ...] = (
    "JobTemplate",
    "WorkflowJobTemplate",
    "Project",
    "InventorySource",
)
"""Kinds AWX exposes through ``unified_job_templates`` (schedule parents)."""

# PascalCase -> snake, acronym-aware: ``JobTemplate`` -> ``job_template``,
# ``HTTPRequest`` -> ``http_request`` (a naive split-before-every-capital
# would mangle the latter into ``h_t_t_p_request``).
_CAMEL_TAIL = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_RUN = re.compile(r"([a-z0-9])([A-Z])")


def snake_kind(kind: str) -> str:
    """``JobTemplate`` -> ``job_template`` (AWX's ``type`` spelling)."""
    return _CAMEL_RUN.sub(r"\1_\2", _CAMEL_TAIL.sub(r"\1_\2", kind)).lower()


RESOURCE_OUTCOME_PIPE_KINDS: dict[str, str] = {"copy": "awx.copy_outcome"}
"""Command -> outcome pipe kind naming one resource by ``id`` plus its ``kind``.

A kind offering the command also accepts these records wherever it selects
from ``--stdin``, so a copy pipes straight into ``patch``.
"""


def pipe_kind(kind: str) -> str:
    """The ``awx.<snake_kind>`` ``--format pipe`` hint for ``kind``."""
    return f"awx.{snake_kind(kind)}"


def type_matches_kind(awx_type: str, kind: str) -> bool:
    """Whether an AWX ``type`` (``job_template``) names ``kind`` (``JobTemplate``)."""
    return awx_type.replace("_", "").casefold() == kind.casefold()


def unified_template_kind(awx_type: str | None) -> str | None:
    """The unified-template kind an AWX ``type`` names, or ``None`` if it is not one."""
    if not awx_type:
        return None
    return next(
        (kind for kind in UNIFIED_TEMPLATE_KINDS if type_matches_kind(awx_type, kind)), None
    )


__all__ = [
    "UNIFIED_TEMPLATE_KINDS",
    "pipe_kind",
    "snake_kind",
    "type_matches_kind",
    "unified_template_kind",
]
