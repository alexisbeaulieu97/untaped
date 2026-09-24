"""Execution ids for ``awx jobs`` commands: positional ids, bare stdin, or typed records."""

from __future__ import annotations

from typing import Annotated, Literal

from cyclopts import Parameter

from untaped.capabilities.awx.domain.job import JOB_ROUTES
from untaped.capability_api import ConfigError, read_identifiers, read_stdin_input

JOB_KIND_HELP = (
    "Execution kind. Defaults to 'job' (a JobTemplate run). Other values: "
    "workflow_job, project_update, inventory_update, ad_hoc_command. "
    "Hits the matching AWX collection (e.g. workflow_jobs/<id>/)."
)


JOB_PIPE_KINDS = frozenset(
    {"awx.job", "awx.launch_outcome", "awx.sync_outcome", "awx.relaunch_outcome"}
)
"""Pipe kinds that carry an execution id: job records and launch/sync/relaunch results."""

JobIdsArgument = Annotated[list[str] | None, Parameter(help="AWX job ids.")]

JobKind = Literal["job", "workflow_job", "project_update", "inventory_update", "ad_hoc_command"]

JobKindOption = Annotated[JobKind, Parameter(name="--kind", help=JOB_KIND_HELP)]

JobsStdinOption = Annotated[
    bool, Parameter(name="--stdin", negative="", help="Read job ids from stdin (one per line).")
]


def job_targets(
    job_ids: list[str] | None, *, stdin: bool, kind: JobKind
) -> tuple[list[str], dict[str, str]]:
    """Identifiers plus each one's execution kind.

    Typed pipe records carry their own execution kind (``kind`` on action
    rows, ``type`` on AWX job records); it wins over ``--kind`` so a piped
    workflow job is read from ``workflow_jobs/`` rather than 404ing on
    ``jobs/``. Bare identifiers use ``--kind``.
    """
    if stdin and not job_ids:
        piped = read_stdin_input(accept_kinds=JOB_PIPE_KINDS)
        if piped.records is None:
            return list(piped.values), {}
        ids: list[str] = []
        kinds: dict[str, str] = {}
        for envelope in piped.records:
            record = envelope.record
            ident = str(record.get("id"))
            ids.append(ident)
            record_kind = record.get("kind")
            if record_kind not in JOB_ROUTES:
                record_kind = record.get("type")
            if isinstance(record_kind, str) and record_kind in JOB_ROUTES:
                kinds[ident] = record_kind
        return ids, kinds
    ids = read_identifiers(
        list(job_ids or []), stdin=stdin, id_field="id", accept_kinds=JOB_PIPE_KINDS
    )
    return ids, {}


def as_job_id(value: str) -> int:
    """Parse a stdin/positional job id; surface bad input as a per-id error.

    ``resolve_each`` only catches :class:`UntapedError`, so a raw
    ``ValueError`` from a malformed stdin line would otherwise abort the
    batch — wrapping in :class:`ConfigError` lets the loop continue and
    emit a per-id ``error:`` row for the bad value alongside the good ones.
    """
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"not a numeric job id: {value!r}") from exc
