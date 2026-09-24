"""Map a resource spec to its ``--format pipe`` ``kind`` hint.

``ResourceSpec.kind`` is PascalCase (e.g. ``JobTemplate``); the untaped
wire ``kind`` convention is lowercase, dot-namespaced, snake_case for
multi-word entities (``awx.job_template``); the transform lives in
:mod:`untaped.capabilities.awx.domain.kinds`.
"""

from __future__ import annotations

from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.domain.kinds import pipe_kind


def pipe_kind_for_spec(spec: ResourceSpec) -> str:
    """Return the ``awx.<snake_kind>`` pipe hint for ``spec``."""
    return pipe_kind(spec.kind)


def id_field_for(spec: ResourceSpec, *, by_id: bool) -> str:
    """The record field a ``--stdin`` consumer extracts for ``spec``.

    ``--by-id`` resolves on the numeric ``id``; otherwise on the spec's
    primary identity key (its name field). Keeps the
    ``--format pipe`` → ``--stdin`` bridge consistent across the factories.
    """
    return "id" if by_id else spec.identity_keys[0]


__all__ = ["id_field_for", "pipe_kind_for_spec"]
