"""Map a resource spec to its ``--format pipe`` ``kind`` hint.

``ResourceSpec.kind`` is PascalCase (e.g. ``JobTemplate``); the untaped
wire ``kind`` convention is lowercase, dot-namespaced, snake_case for
multi-word entities (``awx.job_template``); the transform lives in
:mod:`untaped_awx.domain.kinds`.
"""

from __future__ import annotations

from untaped.sdk import read_identifiers, read_stdin_input
from untaped_awx.application.selection import pipe_record_id
from untaped_awx.domain import ResourceSpec
from untaped_awx.domain.kinds import RESOURCE_OUTCOME_PIPE_KINDS, pipe_kind
from untaped_awx.infrastructure.spec import AwxResourceSpec


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


def template_targets(
    spec: ResourceSpec, identifiers: list[str] | None, *, stdin: bool, by_id: bool
) -> list[tuple[str, bool]]:
    """Targets of a template read command, each with its own ``by_id`` mode.

    Positional identifiers and bare stdin lines are names, or ids with
    ``--by-id``. A piped record of ``spec``'s kind always names its template
    by ``id`` (like the selection commands), so a name shared across
    organizations can't send the lookup to the wrong template.
    """
    if not stdin or identifiers:
        return [
            (target, by_id) for target in read_identifiers(list(identifiers or []), stdin=stdin)
        ]
    piped = read_stdin_input(accept_kinds={pipe_kind_for_spec(spec)})
    if piped.records is None:
        return [(value, by_id) for value in piped.values]
    return [(str(pipe_record_id(envelope)), True) for envelope in piped.records]


__all__ = ["pipe_kind_for_spec", "selection_pipe_kinds", "template_targets"]
