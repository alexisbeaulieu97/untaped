"""Targets of the template read commands: names, ``--by-id`` ids, or typed records."""

from __future__ import annotations

from untaped.sdk import read_identifiers, read_stdin_input
from untaped_awx.application.selection import pipe_record_id
from untaped_awx.cli.pipe import pipe_kind_for_spec
from untaped_awx.domain import ResourceSpec


def template_targets(
    spec: ResourceSpec, identifiers: list[str] | None, *, stdin: bool, by_id: bool
) -> list[tuple[str, bool]]:
    """Targets of a template read command, each with its own ``by_id`` mode.

    Positional identifiers and bare stdin lines are names, or ids with
    ``--by-id``. A piped record (of ``spec``'s kind, or with no kind) always
    names its template by ``id``, like the selection commands, so a name
    shared across organizations can't send the lookup to the wrong template.
    """
    if not stdin or identifiers:
        return [
            (target, by_id) for target in read_identifiers(list(identifiers or []), stdin=stdin)
        ]
    piped = read_stdin_input(accept_kinds={pipe_kind_for_spec(spec)})
    if piped.records is None:
        return [(value, by_id) for value in piped.values]
    return [(str(pipe_record_id(envelope)), True) for envelope in piped.records]


__all__ = ["template_targets"]
