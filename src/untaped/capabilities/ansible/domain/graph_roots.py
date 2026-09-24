"""Graph roots read from stdin: bare ``owner/repo@ref`` lines or pipe records.

A bare line is a repository reference (``owner/repo``, a Git URL, an alias)
with an optional ``@ref`` after an ``owner/repo`` slug. A pipe record names
its repository in the first present field of :data:`REPO_FIELDS` and its ref
in the first present field of :data:`REF_FIELDS`; an empty ref means the
repository's default branch.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

REPO_FIELDS: tuple[str, ...] = ("scm_url", "repo_url", "repo", "full_name")
"""Record fields that may carry a root's repository, in precedence order."""

REF_FIELDS: tuple[str, ...] = ("effective_scm_ref", "ref")
"""Record fields that may carry a root's ref, in precedence order."""

_SLUG_WITH_REF = re.compile(r"^(?P<repo>[^/@:\s]+/[^/@:\s]+)@(?P<ref>\S.*)$")


class GraphRoot(BaseModel):
    """One requested root: an unresolved repository reference plus its ref."""

    model_config = ConfigDict(frozen=True)

    target: str
    ref: str | None = None


def root_from_line(text: str) -> GraphRoot:
    """Parse ``owner/repo@ref``; anything else is a ref-less reference."""
    line = text.strip()
    match = _SLUG_WITH_REF.match(line)
    if match is None:
        return GraphRoot(target=line)
    return GraphRoot(target=match["repo"], ref=match["ref"])


def root_from_record(record: Mapping[str, object]) -> GraphRoot | None:
    """The root a pipe record names, or ``None`` when it has no repository."""
    target = next(
        (value for field in REPO_FIELDS if isinstance(value := record.get(field), str) and value),
        None,
    )
    if target is None:
        return None
    ref = next(
        (value for field in REF_FIELDS if isinstance(value := record.get(field), str)),
        None,
    )
    return GraphRoot(target=target, ref=ref or None)


__all__ = ["REF_FIELDS", "REPO_FIELDS", "GraphRoot", "root_from_line", "root_from_record"]
