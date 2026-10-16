"""The record models a resource command reads from ``--stdin``.

A kind's rows are its spec's record model (``ResourceSpec.record``), which
declares the ``--format pipe`` kind once (``awx.job_template``); readers
name the models, never the strings.
"""

from __future__ import annotations

from untaped.sdk import Record
from untaped_awx.domain import ResourceSpec
from untaped_awx.domain.outcomes import RESOURCE_OUTCOMES
from untaped_awx.infrastructure.spec import AwxResourceSpec


def selection_records(spec: ResourceSpec) -> tuple[type[Record], ...]:
    """The records ``--stdin`` selection of ``spec`` accepts.

    The kind's own records, plus the outcome records of the commands it
    offers that name one resource (``copy`` -> ``CopyOutcome``).
    """
    commands = spec.commands if isinstance(spec, AwxResourceSpec) else ()
    return (
        spec.require_record(),
        *(model for command, model in RESOURCE_OUTCOMES.items() if command in commands),
    )


__all__ = ["selection_records"]
