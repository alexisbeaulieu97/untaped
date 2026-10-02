"""Map a resource spec to its ``--format pipe`` ``kind`` hint.

``ResourceSpec.kind`` is PascalCase (e.g. ``JobTemplate``); the untaped
wire ``kind`` convention is lowercase, dot-namespaced, snake_case for
multi-word entities (``awx.job_template``); the transform lives in
:mod:`untaped.capabilities.awx.domain.kinds`.
"""

from __future__ import annotations

from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.domain.kinds import RESOURCE_OUTCOME_PIPE_KINDS, pipe_kind
from untaped.capabilities.awx.infrastructure.spec import AwxResourceSpec


def pipe_kind_for_spec(spec: ResourceSpec) -> str:
    """Return the ``awx.<snake_kind>`` pipe hint for ``spec``."""
    return pipe_kind(spec.kind)


def selection_pipe_kinds(spec: ResourceSpec) -> set[str]:
    """Pipe kinds ``--stdin`` selection of ``spec`` accepts.

    The kind's own records, plus the outcome records of the commands it
    offers that name one resource (``copy`` -> ``awx.copy_outcome``).
    """
    commands = spec.commands if isinstance(spec, AwxResourceSpec) else ()
    return {pipe_kind(spec.kind)} | {
        kind for command, kind in RESOURCE_OUTCOME_PIPE_KINDS.items() if command in commands
    }


def id_field_for(spec: ResourceSpec, *, by_id: bool) -> str:
    """The record field a ``--stdin`` consumer extracts for ``spec``.

    ``--by-id`` resolves on the numeric ``id``; otherwise on the spec's
    primary identity key (its name field). Keeps the
    ``--format pipe`` → ``--stdin`` bridge consistent across the factories.
    """
    return "id" if by_id else spec.identity_keys[0]


__all__ = ["id_field_for", "pipe_kind_for_spec", "selection_pipe_kinds"]
