"""Add a job template's SCM source and effective ref from its project.

A job template runs its project's repository at a ref: the template's own
``scm_branch`` when it sets one and the project allows the override,
otherwise the project's ``scm_branch``. Empty stays empty; the repository's
default branch is never guessed. Each distinct project is read once.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from untaped.capabilities.awx.application.ports import Catalog, ResourceClient
from untaped.capabilities.awx.domain.payloads import as_dict
from untaped.capabilities.awx.errors import AwxApiError

SCM_FIELDS: tuple[str, ...] = ("scm_url", "effective_scm_ref", "project_allow_override")
"""Fields :func:`with_scm` adds to every record, in this order."""


def effective_scm_ref(template: Mapping[str, Any], project: Mapping[str, Any]) -> str | None:
    """The ref a job of ``template`` checks out from ``project``."""
    own = template.get("scm_branch")
    if own and project.get("allow_override"):
        return str(own)
    ref = project.get("scm_branch")
    return None if ref is None else str(ref)


def with_scm(
    records: Iterable[dict[str, Any]],
    *,
    client: ResourceClient,
    catalog: Catalog,
    warn: Callable[[str], None],
) -> list[dict[str, Any]]:
    """Return copies of job template ``records`` carrying :data:`SCM_FIELDS`.

    A template without a project, or whose project cannot be read, gets
    ``null`` values (the latter with one warning per project).
    """
    rows = [dict(record) for record in records]
    project_spec = catalog.get("Project")
    projects: dict[int, Mapping[str, Any] | None] = {}
    for row in rows:
        project_id = row.get("project")
        if isinstance(project_id, int) and project_id not in projects:
            try:
                projects[project_id] = as_dict(client.get(project_spec, project_id))
            except AwxApiError as exc:
                warn(f"project {project_id}: {exc}")
                projects[project_id] = None
    for row in rows:
        project_id = row.get("project")
        project = projects.get(project_id) if isinstance(project_id, int) else None
        if project is None:
            row.update(dict.fromkeys(SCM_FIELDS))
            continue
        row["scm_url"] = project.get("scm_url")
        row["effective_scm_ref"] = effective_scm_ref(row, project)
        allow = project.get("allow_override")
        row["project_allow_override"] = None if allow is None else bool(allow)
    return rows


__all__ = ["SCM_FIELDS", "effective_scm_ref", "with_scm"]
