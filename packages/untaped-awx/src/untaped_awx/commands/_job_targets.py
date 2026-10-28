"""Execution ids for ``awx jobs`` commands: positional ids, bare stdin, or typed records."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from untaped.sdk import ConfigError, Positional, Stdin
from untaped_awx.commands._awx import AwxCommand
from untaped_awx.domain.jobs.job import JOB_ROUTES
from untaped_awx.records import JobRecord, JobRelaunchOutcome, LaunchOutcome, SyncOutcome

JOB_KIND_HELP = (
    "Execution kind. Defaults to 'job' (a JobTemplate run). Other values: "
    "workflow_job, project_update, inventory_update, ad_hoc_command. "
    "Hits the matching AWX collection (e.g. workflow_jobs/<id>/)."
)

JobKind = Literal["job", "workflow_job", "project_update", "inventory_update", "ad_hoc_command"]


class JobsCommand(AwxCommand):
    """A command on existing executions, named by id or by piped awx records.

    Piped records (jobs, and launch, sync and relaunch results) carry their
    own execution kind, which wins over ``--kind``.
    """

    job_ids: Positional[list[str]] = Field(default_factory=list, description="AWX job ids.")
    stdin: Stdin[JobRecord | LaunchOutcome | SyncOutcome | JobRelaunchOutcome]
    kind: JobKind = Field("job", description=JOB_KIND_HELP)

    def targets(self) -> tuple[list[str], dict[str, str]]:
        """The ids, and the execution kind of each piped one (others take ``--kind``)."""
        return job_targets(self.job_ids, self.stdin)


def job_targets(job_ids: Sequence[str], stdin: Stdin[Any]) -> tuple[list[str], dict[str, str]]:
    """Identifiers plus each one's execution kind.

    Typed pipe records carry their own execution kind (``kind`` on action
    rows, ``type`` on AWX job records); it wins over ``--kind`` so a piped
    workflow job is read from ``workflow_jobs/`` rather than 404ing on
    ``jobs/``. Bare identifiers get no entry: callers fall back to ``--kind``.
    """
    if not stdin.requested:
        return stdin.identifiers(job_ids), {}
    if job_ids:
        stdin.identifiers(job_ids)  # ids and --stdin at once: its usage error
    ids: list[str] = []
    kinds: dict[str, str] = {}
    for value in stdin.values():
        if isinstance(value, str):
            ids.append(value)
            continue
        record = value.model_dump(mode="json", by_alias=True)
        ident = str(record.get("id"))
        ids.append(ident)
        record_kind = record.get("kind")
        if record_kind not in JOB_ROUTES:
            record_kind = record.get("type")
        if isinstance(record_kind, str) and record_kind in JOB_ROUTES:
            kinds[ident] = record_kind
    return ids, kinds


def as_job_id(value: str) -> int:
    """Parse a stdin/positional job id; surface bad input as a per-id error.

    A :class:`ConfigError` (not a raw ``ValueError``) lets a batch go on and
    report the bad value as its own failure beside the good ones.
    """
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"not a numeric job id: {value!r}", category="invalid") from exc
