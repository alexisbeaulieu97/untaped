"""``jobs cancel`` and ``jobs relaunch``: confirmed writes on existing executions.

Both read every target first (a missing id rejects the whole batch before
any POST), preview each target on stderr, ask once with No as the default
(``--yes`` skips the prompt, ``--dry-run`` wins), and emit one outcome
record per target.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any

from cyclopts import App, Parameter

from untaped.capabilities.awx.cli.context import AwxContext, open_context
from untaped.capabilities.awx.cli.job_targets import (
    JOB_KIND_HELP,
    JobIdsArgument,
    JobKind,
    as_job_id,
    job_targets,
)
from untaped.capabilities.awx.cli.options import DryRunOption, YesOption
from untaped.capabilities.awx.domain.job import JOB_ROUTES, TERMINAL_STATUSES
from untaped.capabilities.awx.domain.outcomes import JobCancelOutcome, JobRelaunchOutcome
from untaped.capability_api import (
    ColumnsOption,
    FormatOption,
    OperationCancelledError,
    UntapedError,
    echo,
    emit,
    finish,
    plural,
    q,
    raise_usage,
    report_errors,
    resolve_each,
)

_StdinOption = Annotated[
    bool,
    Parameter(name="--stdin", negative="", help="Read job ids from stdin (one per line)."),
]
_KindOption = Annotated[JobKind, Parameter(name="--kind", help=JOB_KIND_HELP)]


def register_job_actions(jobs_app: App) -> None:
    """Attach ``cancel`` and ``relaunch`` to the ``awx jobs`` group."""

    @jobs_app.command(name="cancel")
    def jobs_cancel(
        job_ids: JobIdsArgument = None,
        /,
        *,
        stdin: _StdinOption = False,
        kind: _KindOption = "job",
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Cancel running executions after one preview and confirmation.

        Finished executions are skipped. AWX stops a job asynchronously, so a
        successful row reports ``cancel_requested``; ``jobs wait`` shows when it
        reached ``canceled``.
        """
        rows: list[JobCancelOutcome] = []
        with report_errors(), open_context() as ctx:
            ids, kinds = job_targets(job_ids, stdin=stdin, kind=kind)
            rows = [_cancel_plan(k, r) for k, r in _read_targets(ctx, ids, kinds, kind)]
            for row in rows:
                if row.action == "planned":
                    echo(f"cancel {_label(row.kind, row.id, row.name)} ({row.status})", err=True)
                else:
                    echo(f"skipped: {_label(row.kind, row.id, row.name)}: {row.detail}", err=True)
            pending = [index for index, row in enumerate(rows) if row.action == "planned"]
            if pending and not dry_run:
                _confirm(ctx, verb="cancel", count=len(pending), yes=yes)
                for index in pending:
                    rows[index] = _cancel_one(ctx, rows[index])
        if rows:
            emit(rows, fmt=fmt, columns=columns, kind="awx.cancel_outcome")
        finish(any(row.failed for row in rows))

    @jobs_app.command(name="relaunch")
    def jobs_relaunch(
        job_ids: JobIdsArgument = None,
        /,
        *,
        stdin: _StdinOption = False,
        kind: _KindOption = "job",
        failed_hosts: Annotated[
            bool,
            Parameter(
                name="--failed-hosts",
                negative="",
                help="Rerun only the hosts that failed (job executions only).",
            ),
        ] = False,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Relaunch executions as new jobs after one preview and confirmation.

        Each row's ``id`` and ``kind`` name the new execution and
        ``target_id`` the one it repeats, so ``--format pipe`` feeds
        ``jobs wait --stdin`` or ``jobs logs --stdin``.
        """
        hosts = "failed" if failed_hosts else "all"
        rows: list[JobRelaunchOutcome] = []
        with report_errors(), open_context() as ctx:
            ids, kinds = job_targets(job_ids, stdin=stdin, kind=kind)
            for ident in ids:
                _check_relaunchable(kinds.get(ident, kind), failed_hosts=failed_hosts)
            for job_kind, record in _read_targets(ctx, ids, kinds, kind):
                row = JobRelaunchOutcome(
                    kind=job_kind,
                    name=record.get("name"),
                    target_id=int(record["id"]),
                    hosts=hosts,
                    action="planned",
                )
                rows.append(row)
                echo(
                    f"relaunch {_label(job_kind, row.target_id, row.name)} "
                    f"({record.get('status')}) hosts={hosts}",
                    err=True,
                )
            if rows and not dry_run:
                _confirm(ctx, verb="relaunch", count=len(rows), yes=yes)
                rows = [_relaunch_one(ctx, row) for row in rows]
        if rows:
            emit(rows, fmt=fmt, columns=columns, kind="awx.relaunch_outcome")
        finish(any(row.failed for row in rows))


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

    _records, failed = resolve_each(list(ids), read)
    if failed:
        finish(True)
    return targets


def _label(kind: str, id_: int | None, name: str | None) -> str:
    return f"{kind} {id_}" + (f" {q(name)}" if name else "")


def _confirm(ctx: AwxContext, *, verb: str, count: int, yes: bool) -> None:
    confirmed = ctx.progress_ui().confirm_action(
        f"{verb.capitalize()} {plural(count, 'job')}?",
        assume_yes=yes,
        refusal=f"{verb} requires --yes or --dry-run when not interactive",
    )
    if not confirmed:
        raise OperationCancelledError


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
        echo(f"failed: {_label(row.kind, row.id, row.name)}: {exc}", err=True)
        return row.model_copy(update={"action": "failed", "detail": str(exc)})
    return row.model_copy(update={"action": "cancel_requested"})


def _check_relaunchable(kind: str, *, failed_hosts: bool) -> None:
    if not JOB_ROUTES[kind].relaunch:
        raise_usage(f"{kind} executions cannot be relaunched; sync or launch the template instead")
    if failed_hosts and kind != "job":
        raise_usage(f"--failed-hosts applies only to job executions, not {kind}")


def _relaunch_one(ctx: AwxContext, row: JobRelaunchOutcome) -> JobRelaunchOutcome:
    try:
        created = ctx.jobs.relaunch(
            kind=row.kind,
            job_id=row.target_id,
            hosts="failed" if row.hosts == "failed" else None,
        )
    except UntapedError as exc:
        echo(f"failed: {_label(row.kind, row.target_id, row.name)}: {exc}", err=True)
        return row.model_copy(update={"action": "failed", "detail": str(exc)})
    new_kind = created.get("type")
    return row.model_copy(
        update={
            "id": created.get("id"),
            "kind": new_kind if new_kind in JOB_ROUTES else row.kind,
            "status": created.get("status"),
            "action": "relaunched",
        }
    )
