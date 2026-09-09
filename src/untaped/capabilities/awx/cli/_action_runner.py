"""Submit fixed launch/sync targets and preserve execution IDs through monitoring failures."""

from collections import Counter
from collections.abc import Sequence
from typing import Any

from rich.console import Console

from untaped.api import ColumnsOption, FormatOption, echo, emit, finish
from untaped.capabilities.awx.application import RunAction
from untaped.capabilities.awx.application.mutation_values import redact_error
from untaped.capabilities.awx.application.prepare_actions import prepare_action_targets
from untaped.capabilities.awx.application.selected_actions import run_selected_actions
from untaped.capabilities.awx.application.selection import SelectedResource
from untaped.capabilities.awx.cli._context import AwxContext
from untaped.capabilities.awx.cli._parallel import _drain_parallel, _wait_parallel
from untaped.capabilities.awx.domain import Job, ResourceSpec
from untaped.capabilities.awx.errors import ActionResponseError


def run_action_selection(
    ctx: AwxContext,
    spec: ResourceSpec,
    selected: Sequence[SelectedResource],
    *,
    action: str,
    payload: dict[str, Any] | None = None,
    dry_run: bool = False,
    parallel: int = 1,
    continue_on_error: bool = False,
    wait: bool = False,
    track: bool = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """One bounded POST phase, then monitor every known execution even after failures."""
    spec, targets = prepare_action_targets(ctx.repo, ctx.catalog, spec, selected, action=action)
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
    if dry_run:
        emit(rows, fmt=fmt, columns=columns, kind="awx.job")
        return

    def safe_error(exc: Exception, target: SelectedResource) -> str:
        return _action_error(exc, spec, target, payload)

    outcomes = run_selected_actions(
        targets,
        lambda item: RunAction(ctx.repo).execute(spec, item.id, action=action, payload=payload),
        parallel=parallel,
        continue_on_error=continue_on_error,
        error_detail=safe_error,
    )
    launched: list[tuple[str, Job]] = []
    row_by_label: dict[str, int] = {}
    labels = _monitor_labels(targets)
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
        console = Console(stderr=True, highlight=False)
        finals, errors = (
            _drain_parallel(ctx.monitor, launched, console)
            if track
            else _wait_parallel(ctx.repo, launched)
        )
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
        secrets["extra_vars"] = [
            part.partition("=")[2] for part in secrets["extra_vars"].splitlines()
        ]
    secret_spec = spec.model_copy(
        update={"secret_paths": (*spec.secret_paths, "extra_vars", "extra_vars.*")}
    )
    return redact_error(exc, secret_spec, target.record, payload or {}, secrets)


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
