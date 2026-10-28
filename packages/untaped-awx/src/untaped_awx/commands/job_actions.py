"""``jobs cancel`` and ``jobs relaunch``: confirmed writes on existing executions.

Both read every target first (a missing id rejects the whole batch before
any POST), preview each target on stderr, ask once with No as the default
(``--yes`` skips the prompt, ``--dry-run`` wins), and print one outcome
record per target.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from pydantic import Field

from untaped.sdk import (
    Confirm,
    Failed,
    Failure,
    Output,
    UntapedError,
    UsageError,
    key,
    plural,
    q,
)
from untaped_awx.application.resources.mutation_values import error_text
from untaped_awx.commands._job_targets import JobsCommand, as_job_id
from untaped_awx.commands.context import AwxContext
from untaped_awx.domain.jobs.job import JOB_ROUTES, TERMINAL_STATUSES
from untaped_awx.records import JobCancelOutcome, JobRelaunchOutcome

_CANCEL_COLUMNS = (
    key(JobCancelOutcome).id,
    key(JobCancelOutcome).name,
    key(JobCancelOutcome).action,
    key(JobCancelOutcome).detail,
)
"""A cancel row's ``status`` is the one read before the request, so the table leaves it out."""
_RELAUNCH_COLUMNS = (
    key(JobRelaunchOutcome).id,
    key(JobRelaunchOutcome).name,
    key(JobRelaunchOutcome).status,
    key(JobRelaunchOutcome).target_id,
    key(JobRelaunchOutcome).action,
    key(JobRelaunchOutcome).detail,
)


class Cancel(JobsCommand):
    """Cancel running executions after one preview and confirmation.

    Every id is read first, and an unknown id rejects the batch. AWX stops
    a job asynchronously, so a successful `awx.job_cancel_outcome` row reports
    `cancel_requested`; `jobs wait` shows when it reached `canceled`. A
    finished execution is `skipped`, and one AWX refuses is `failed`. A
    row's `status` is the one read before the request.

    Examples
    --------
    ```console
    $ untaped awx jobs cancel 101 102 --dry-run
    ```
    """

    confirm: Confirm
    output: Output[JobCancelOutcome] = Output(columns=_CANCEL_COLUMNS)

    def run(self) -> Iterator[JobCancelOutcome]:
        with self.awx() as awx:
            ids, kinds = self.targets()
            rows = [_cancel_plan(k, r) for k, r in _read_targets(awx, ids, kinds, self.kind)]
            for row in rows:
                if row.action != "planned":
                    awx.diag.warning(f"{_label(row.kind, row.id, row.name)}: skipped, {row.detail}")
            pending = [index for index, row in enumerate(rows) if row.action == "planned"]
            preview = [
                f"cancel {_label(rows[i].kind, rows[i].id, rows[i].name)} ({rows[i].status})"
                for i in pending
            ]
            question = f"Cancel {plural(len(pending), 'job')}?"
            if pending and self.confirm(question, preview=lambda: preview):
                for index in pending:
                    rows[index] = _cancel_one(awx, rows[index])
        yield from rows
        if any(row.failed for row in rows):
            raise Failed


class Relaunch(JobsCommand):
    """Relaunch executions as new jobs after one preview and confirmation.

    Every id is read first, and an unknown id rejects the batch. Each
    row's `id` and `kind` name the new execution and `target_id` the one
    it repeats, so `--format pipe` feeds `jobs wait --stdin` or `jobs
    logs --stdin`. Project and inventory updates cannot be relaunched:
    sync them instead.

    Examples
    --------
    ```console
    $ untaped awx jobs relaunch 101 --failed-hosts --yes -f pipe | untaped awx jobs wait --stdin
    ```
    """

    failed_hosts: bool = Field(
        False, description="Rerun only the hosts that failed (job executions only)."
    )
    confirm: Confirm
    output: Output[JobRelaunchOutcome] = Output(columns=_RELAUNCH_COLUMNS)

    def run(self) -> Iterator[JobRelaunchOutcome]:
        hosts = "failed" if self.failed_hosts else "all"
        rows: list[JobRelaunchOutcome] = []
        preview: list[str] = []
        with self.awx() as awx:
            ids, kinds = self.targets()
            for ident in ids:
                _check_relaunchable(kinds.get(ident, self.kind), failed_hosts=self.failed_hosts)
            for job_kind, record in _read_targets(awx, ids, kinds, self.kind):
                row = JobRelaunchOutcome(
                    kind=job_kind,
                    name=record.get("name"),
                    target_id=int(record["id"]),
                    hosts=hosts,
                    action="planned",
                )
                rows.append(row)
                preview.append(
                    f"relaunch {_label(job_kind, row.target_id, row.name)} "
                    f"({record.get('status')}) hosts={hosts}"
                )
            question = f"Relaunch {plural(len(rows), 'job')}?"
            if rows and self.confirm(question, preview=lambda: preview):
                rows = [_relaunch_one(awx, row) for row in rows]
        yield from rows
        if any(row.failed for row in rows):
            raise Failed


def _read_targets(
    ctx: AwxContext, ids: Sequence[str], kinds: dict[str, str], kind: str
) -> list[tuple[str, dict[str, Any]]]:
    """Every target's ``(kind, record)``; any unreadable id rejects the batch (exit 1)."""
    targets: list[tuple[str, dict[str, Any]]] = []

    def read(ident: str) -> dict[str, Any]:
        job_kind = kinds.get(ident, kind)
        record = ctx.jobs.get(kind=job_kind, job_id=as_job_id(ident))
        targets.append((job_kind, record))
        return record

    failed = False
    for ident in ids:
        try:
            read(ident)
        except UntapedError as exc:
            ctx.diag.failure(exc, item=ident)
            failed = True
    if failed:
        raise Failed("an execution could not be read; nothing was changed")
    return targets


def _label(kind: str, id_: int | None, name: str | None) -> str:
    return f"{kind} {id_}" + (f" {q(name)}" if name else "")


def _cancel_plan(kind: str, record: dict[str, Any]) -> JobCancelOutcome:
    status = record.get("status")
    finished = status in TERMINAL_STATUSES
    return JobCancelOutcome(
        id=int(record["id"]),
        kind=kind,
        name=record.get("name"),
        status=status,
        action="skipped" if finished else "planned",
        detail=f"already {status}" if finished else None,
    )


def _cancel_one(ctx: AwxContext, row: JobCancelOutcome) -> JobCancelOutcome:
    try:
        ctx.jobs.cancel(kind=row.kind, job_id=row.id)
    except UntapedError as exc:
        ctx.diag.failure(exc, item=_label(row.kind, row.id, row.name))
        return row.model_copy(update=_failure(exc))
    return row.model_copy(update={"action": "cancel_requested"})


def _failure(exc: UntapedError) -> dict[str, Any]:
    """The ``action``, ``detail`` and ``error`` of a row ``exc`` failed."""
    detail = error_text(exc)
    return {
        "action": "failed",
        "detail": detail,
        "error": Failure.from_exception(exc, message=detail),
    }


def _check_relaunchable(kind: str, *, failed_hosts: bool) -> None:
    if not JOB_ROUTES[kind].relaunch:
        raise UsageError(
            f"{kind} executions cannot be relaunched; sync or launch the template instead"
        )
    if failed_hosts and kind != "job":
        raise UsageError(f"--failed-hosts applies only to job executions, not {kind}")


def _relaunch_one(ctx: AwxContext, row: JobRelaunchOutcome) -> JobRelaunchOutcome:
    try:
        created = ctx.jobs.relaunch(
            kind=row.kind,
            job_id=row.target_id,
            hosts="failed" if row.hosts == "failed" else None,
        )
    except UntapedError as exc:
        ctx.diag.failure(exc, item=_label(row.kind, row.target_id, row.name))
        return row.model_copy(update=_failure(exc))
    new_kind = created.get("type")
    return row.model_copy(
        update={
            "id": created.get("id"),
            "kind": new_kind if new_kind in JOB_ROUTES else row.kind,
            "status": created.get("status"),
            "action": "relaunched",
        }
    )
