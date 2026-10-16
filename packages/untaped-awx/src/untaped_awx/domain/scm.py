"""The ref a job template's job checks out from its project."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def effective_scm_ref(template: Mapping[str, Any], project: Mapping[str, Any]) -> str | None:
    """The ref a job of ``template`` checks out from ``project``.

    The template's own ``scm_branch`` when it sets one and the project allows
    the override, otherwise the project's ``scm_branch``; ``None`` when the
    project has none.
    """
    own = template.get("scm_branch")
    if own and project.get("allow_override"):
        return str(own)
    ref = project.get("scm_branch")
    return None if ref is None else str(ref)


__all__ = ["effective_scm_ref"]
