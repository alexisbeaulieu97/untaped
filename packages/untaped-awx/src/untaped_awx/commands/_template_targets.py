"""Targets of the template read commands: names, ``--by-id`` ids, or typed records."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from untaped.sdk import Stdin
from untaped_awx.application.resources.selection import pipe_record_id


def template_targets(
    identifiers: Sequence[str], stdin: Stdin[Any], *, by_id: bool
) -> list[tuple[str, bool]]:
    """Targets of a template read command, each with its own ``by_id`` mode.

    Positional identifiers and bare stdin lines are names, or ids with
    ``--by-id``. A piped record (of the template's kind) always names its
    template by ``id``, like the selection commands, so a name shared across
    organizations can't send the lookup to the wrong template.
    """
    if not stdin.requested:
        return [(target, by_id) for target in stdin.identifiers(identifiers)]
    if identifiers:
        stdin.identifiers(identifiers)  # names and --stdin at once: its usage error
    return [
        (str(pipe_record_id(value)), True) if isinstance(value, BaseModel) else (value, by_id)
        for value in stdin.values()
    ]


__all__ = ["template_targets"]
