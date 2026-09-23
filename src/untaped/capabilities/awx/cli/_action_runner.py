"""Submit fixed launch/sync targets and preserve execution IDs through monitoring failures."""

import json
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any, NoReturn

from rich.console import Console

from untaped.capabilities.awx.application import RunAction
from untaped.capabilities.awx.application.mutation_values import redact_error
from untaped.capabilities.awx.application.prepare_actions import prepare_action_targets
from untaped.capabilities.awx.application.selected_actions import (
    ActionsInterrupted,
    SelectedActionOutcome,
    run_selected_actions,
)
from untaped.capabilities.awx.application.selection import SelectedResource
from untaped.capabilities.awx.cli._context import AwxContext
from untaped.capabilities.awx.cli._mutation_runner import confirm_batch
from untaped.capabilities.awx.cli._parallel import _drain_parallel, _wait_parallel
from untaped.capabilities.awx.cli.format import format_scope
from untaped.capabilities.awx.domain import Job, ResourceSpec
from untaped.capabilities.awx.errors import ActionResponseError, LaunchPromptError
from untaped.capability_api import (
    ColumnsOption,
    FormatOption,
    UntapedError,
    echo,
    emit,
    finish,
    raise_usage,
)


def run_action_selection(
    ctx: AwxContext,
    spec: ResourceSpec,
    selected: Sequence[SelectedResource],
    *,
    action: str,
    payload: dict[str, Any] | None = None,
    dry_run: bool = False,
    yes: bool = False,
    confirm: bool = False,
    parallel: int = 1,
    continue_on_error: bool = False,
    wait: bool = False,
    track: bool = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """One bounded POST phase, then monitor every known execution even after failures.

    ``confirm`` (mass selections) previews the targets and asks once unless
    ``yes``; a declined prompt submits nothing.
    """
    spec, targets = _prepare(ctx, spec, selected, action=action, payload=payload)
    result_kinds = next(a.returns for a in spec.actions if a.name == action)
    result_kind = next(iter(result_kinds)) if len(result_kinds) == 1 else None
    rows: list[dict[str, Any]] = [
        {
            "id": None,
            "kind": result_kind,
            "target_id": item.id,
            "target_kind": item.kind,
            "target_name": item.name,
            "scope": item.scope,
            "action": "preview",
        }
        for item in targets
    ]
    if dry_run or (confirm and not yes and not _confirm_targets(ctx, targets, action=action)):
        emit(rows, fmt=fmt, columns=columns, kind="awx.job")
        return

    def safe_error(exc: Exception, target: SelectedResource) -> str:
        return _action_error(exc, spec, target, payload)

    labels = _monitor_labels(targets)
    outcomes = _submit(
        targets,
        labels,
        lambda item: RunAction(ctx.repo).execute(spec, item.id, action=action, payload=payload),
        parallel=parallel,
        continue_on_error=continue_on_error,
        error_detail=safe_error,
    )
    launched: list[tuple[str, Job]] = []
    row_by_label: dict[str, int] = {}
    for index, outcome in enumerate(outcomes):
        row = rows[index]
        row.update(action=outcome.action, detail=outcome.detail)
        if isinstance(outcome.error, ActionResponseError):
            row.update(id=outcome.error.execution_id, kind=outcome.error.execution_kind)
        if outcome.result is not None:
            row.update(outcome.result.model_dump())
            label = labels[index]
            launched.append((label, outcome.result))
            row_by_label[label] = index
    if launched and (wait or track):
        finals, errors = _monitor(ctx, launched, _unmonitored(outcomes, labels), track=track)
        row_by_job = {(rows[i]["kind"], rows[i]["id"]): i for i in row_by_label.values()}
        for job in finals:
            row = rows[row_by_job[(job.kind, job.id)]]
            row.update(job.model_dump())
            if job.status != "successful":
                row.update(action="failed", detail=f"execution ended with status {job.status}")
        for label, exc in errors:
            index = row_by_label[label]
            rows[index].update(action="failed", detail=safe_error(exc, targets[index]))
    for row in rows:
        if row.get("detail"):
            echo(f"{row['action']}: {row['target_name']}: {row['detail']}", err=True)
    emit(rows, fmt=fmt, columns=columns, kind="awx.job")
    finish(any(row["action"] != "completed" for row in rows))


def _submit(
    targets: Sequence[SelectedResource],
    labels: list[str],
    worker: Callable[[SelectedResource], Job],
    *,
    parallel: int,
    continue_on_error: bool,
    error_detail: Callable[[Exception, SelectedResource], str],
) -> list[SelectedActionOutcome[Job]]:
    """Run the POST phase; Ctrl-C names the executions submitted so far."""
    try:
        return run_selected_actions(
            targets,
            worker,
            parallel=parallel,
            continue_on_error=continue_on_error,
            error_detail=error_detail,
        )
    except ActionsInterrupted as interrupted:
        label_by_target = {id(item): label for item, label in zip(targets, labels, strict=True)}
        report_interrupted(
            [
                (label_by_target[id(outcome.target)], job)
                for outcome in interrupted.outcomes
                if (job := _submitted_execution(outcome)) is not None
            ]
        )


def _monitor(
    ctx: AwxContext,
    launched: list[tuple[str, Job]],
    unmonitored: list[tuple[str, Job]],
    *,
    track: bool,
) -> tuple[list[Job], list[tuple[str, UntapedError]]]:
    """Wait on launched executions; Ctrl-C stops polling and names what still runs."""
    finished: dict[str, Job] = {}
    try:
        if track:
            console = Console(stderr=True, highlight=False)
            return _drain_parallel(ctx.monitor, launched, console, stop=ctx.stop, finished=finished)
        return _wait_parallel(ctx.repo, launched, sleep=ctx.pause, stop=ctx.stop, finished=finished)
    except KeyboardInterrupt:
        report_interrupted(
            [(label, finished.get(label, job)) for label, job in launched] + unmonitored
        )


_ACTIVE_STATUSES = frozenset({"new", "pending", "waiting", "running"})


def report_interrupted(executions: Sequence[tuple[str | None, Job]]) -> NoReturn:
    """Name every execution not known to have ended, with a ``jobs wait`` hint; exit 130.

    A status known locally to be terminal is skipped; an active one "keeps
    running"; an unknown status (e.g. a job AWX created while ignoring
    fields) is reported as "was launched".
    """
    by_kind: dict[str, list[str]] = {}
    for label, job in executions:
        if job.is_terminal:
            continue
        state = "keeps running" if job.status in _ACTIVE_STATUSES else "was launched"
        prefix = f"{label}: " if label else ""
        echo(f"interrupted: {prefix}{job.kind} {job.id} {state}", err=True)
        by_kind.setdefault(job.kind, []).append(str(job.id))
    for kind, ids in by_kind.items():
        echo(f"hint: untaped awx jobs wait {' '.join(ids)} --kind {kind}", err=True)
    raise SystemExit(130)


def _unmonitored(
    outcomes: Sequence[SelectedActionOutcome[Job]], labels: list[str]
) -> list[tuple[str, Job]]:
    """Executions AWX created for failed rows (ignored fields); not waited on."""
    return [
        (labels[index], job)
        for index, outcome in enumerate(outcomes)
        if outcome.result is None and (job := _submitted_execution(outcome)) is not None
    ]


def _submitted_execution(outcome: SelectedActionOutcome[Job]) -> Job | None:
    """The execution an outcome created: its result, or one AWX made despite errors."""
    if outcome.result is not None:
        return outcome.result
    error = outcome.error
    if isinstance(error, ActionResponseError) and error.execution_id is not None:
        return Job(id=error.execution_id, kind=error.execution_kind or "job", status="unknown")
    return None


def _confirm_targets(ctx: AwxContext, targets: Sequence[SelectedResource], *, action: str) -> bool:
    """Preview every target on stderr, then ask once with No as the default."""
    for item in targets:
        echo(
            f"{action} {item.kind}/{item.name} id={item.id} scope={format_scope(item.scope)}",
            err=True,
        )
    if confirm_batch(ctx, count=len(targets), verb=action, yes=False, dry_run=False):
        return True
    echo(f"Cancelled; nothing to {action}.", err=True)
    return False


def _prepare(
    ctx: AwxContext,
    spec: ResourceSpec,
    selected: Sequence[SelectedResource],
    *,
    action: str,
    payload: dict[str, Any] | None,
) -> tuple[ResourceSpec, tuple[SelectedResource, ...]]:
    """Launch-prompt preflight failures are usage errors (exit 2), not API errors."""
    try:
        return prepare_action_targets(
            ctx.repo, ctx.catalog, spec, selected, action=action, payload=payload
        )
    except LaunchPromptError as exc:
        raise_usage(str(exc))


def _action_error(
    exc: Exception,
    spec: ResourceSpec,
    target: SelectedResource,
    payload: dict[str, Any] | None,
) -> str:
    # Action payloads can carry arbitrary secret extra-vars. Suppress both the
    # complete submitted string and individual values in controller exceptions.
    secrets = dict(payload or {})
    if isinstance(secrets.get("extra_vars"), str):
        try:
            decoded = json.loads(secrets["extra_vars"])
        except json.JSONDecodeError:
            decoded = None
        secrets["extra_vars"] = [str(value) for value in _leaf_values(decoded)]
    secret_spec = spec.model_copy(
        update={"secret_paths": (*spec.secret_paths, "extra_vars", "extra_vars.*")}
    )
    return redact_error(exc, secret_spec, target.record, payload or {}, secrets)


def _leaf_values(value: Any) -> list[Any]:
    """Every scalar inside a decoded extra-vars document (bools/None excluded)."""
    if isinstance(value, dict):
        return [leaf for child in value.values() for leaf in _leaf_values(child)]
    if isinstance(value, list):
        return [leaf for child in value for leaf in _leaf_values(child)]
    if value is None or isinstance(value, bool):
        return []
    return [value]


def _monitor_labels(targets: Sequence[SelectedResource]) -> list[str]:
    """Unique display labels must also avoid colliding with literal resource names."""
    counts = Counter(item.name for item in targets)
    reserved = {item.name for item in targets if item.name is not None}
    labels: list[str] = []
    for item in targets:
        if item.name and counts[item.name] == 1:
            label = item.name
        else:
            label = f"{item.name or item.kind}#{item.id}"
            while label in reserved:
                label += "#"
            reserved.add(label)
        labels.append(label)
    return labels
