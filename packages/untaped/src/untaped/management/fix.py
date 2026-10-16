"""``untaped doctor fix``: run every automatic fix doctor found, then re-check.

Doctor's warned and failed rows are grouped by their exact ``fix`` argv, in
row order, so one fix covers every row naming it. An automatic fix runs
after one confirmation (``--yes`` skips it, ``--dry-run`` only plans); a
manual one is ``skipped`` with what it needs; a fix that runs ``doctor`` or
names no root command is refused as ``failed`` before anything runs. Each
fix runs as its own ``python -m untaped <argv> --format json`` with stdin
closed, so a fix that would prompt fails instead of hanging, and is never
killed on a timer (a keychain unlock prompt is a legitimate wait). Doctor's
rows are then collected again: a fix whose rows all pass is ``fixed``, one
that left a row warning or failing is ``partial``. One
``untaped.fix_outcome`` row per fix; the exit code is that of the most
severe failure, and ``1`` when a check still fails afterwards.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import IO, Any

from pydantic import ValidationError

from untaped.batch import batch_apply, finish
from untaped.errors import ErrorCategory, ExitCode, UntapedError
from untaped.management._render import emit_fix_list, emit_fix_plan, emit_isolated
from untaped.management.doctor import (
    collect_doctor_rows,
    group_fixes,
    is_automatic,
    placeholders,
    run_line,
)
from untaped.management.records import FixOutcome
from untaped.messages import command_argv, not_found, run_hint, split_profile, summary
from untaped.output.diagnostic import Failure
from untaped.output.policy import DIAGNOSTICS_ENV, is_verbose
from untaped.output.sink import Diagnostics, count_row_failures, current_sink
from untaped.plugins.registry import ApplicationSpec, CompositionResult
from untaped.profile_resolver import selected_profile
from untaped.theme import OutputFormat
from untaped.ui import ui_context

_diag = Diagnostics()

_ACTIONS = ("fixed", "partial", "failed", "skipped", "planned")
_FORMAT_FLAGS = ("--format", "-f")

Row = dict[str, Any]
Key = tuple[str, str, str]
"""A doctor row's identity: ``(plugin, check, title)``."""


@dataclass(frozen=True)
class _Fix:
    """One fix and the doctor rows it covers."""

    argv: tuple[str, ...]
    keys: tuple[Key, ...]
    checks: tuple[str, ...]
    automatic: bool


@dataclass(frozen=True)
class ChildRun:
    """What one fix's child process left: its exit code, outcome rows and JSON stderr lines."""

    code: int
    rows: list[Row]
    diagnostics: list[Row]


def run_fixes(
    shell: ApplicationSpec,
    result: CompositionResult,
    *,
    online: bool,
    yes: bool,
    dry_run: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
    builtin_for: Callable[[str], str | None],
) -> None:
    """``doctor fix``: collect, select, confirm, run, re-check, report."""
    ui = ui_context(strict=False)
    profile = selected_profile()
    rows = collect_doctor_rows(shell, result, online=online)
    fixes = _select(rows)
    if not fixes:
        _diag.info("nothing to fix")
        _emit([], fmt=fmt, columns=columns, profile=profile)
        _exit_unhealthy(rows, profile)
        return
    refusals = {fix: _refusal(fix, builtin_for) for fix in fixes}
    runnable = [fix for fix in fixes if fix.automatic and refusals[fix] is None]
    manual = [fix for fix in fixes if not fix.automatic and refusals[fix] is None]

    def preview(_planned: Sequence[dict[str, object]]) -> None:
        emit_fix_plan(
            [(_line(fix, profile), list(fix.checks)) for fix in runnable],
            [(_line(fix, profile), list(fix.checks)) for fix in manual],
        )

    outcome = batch_apply(
        runnable,
        _run_one_fix,
        verb="run",
        noun="fix",
        label=lambda fix: _line(fix, profile),
        describe=lambda fix: {"fix": list(fix.argv)},
        ui=ui,
        destructive=True,
        assume_yes=yes,
        preview_only=dry_run,
        preview=preview,
    )
    if outcome.cancelled:
        finish(outcome)
    runs: dict[_Fix, ChildRun | UntapedError] = {**dict(outcome.results), **dict(outcome.failures)}
    after = collect_doctor_rows(shell, result, online=online) if outcome.results else rows
    outcomes = [
        _outcome(fix, refusals[fix], runs.get(fix), after, dry_run=dry_run) for fix in fixes
    ]
    _emit(outcomes, fmt=fmt, columns=columns, profile=profile)
    counts = {action: sum(row.action == action for row in outcomes) for action in _ACTIONS}
    _diag.info(summary("doctor fix", counts))
    if dry_run:
        return
    finish(counts["failed"] > 0 or counts["partial"] > 0)
    _exit_unhealthy(after, profile)


def _select(rows: list[dict[str, object]]) -> list[_Fix]:
    """One :class:`_Fix` per fix argv, grouped as doctor's hint counts them."""
    return [
        _Fix(
            argv=argv,
            keys=tuple(_key(row) for row in covered),
            checks=tuple(dict.fromkeys(str(row["check"]) for row in covered)),
            automatic=is_automatic(covered),
        )
        for argv, covered in group_fixes(rows).items()
    ]


def _key(row: dict[str, object]) -> Key:
    return (str(row["plugin"]), str(row["check"]), str(row["title"]))


def _refusal(fix: _Fix, builtin_for: Callable[[str], str | None]) -> UntapedError | None:
    """Why ``fix`` never runs: it runs ``doctor``, or names no root command (a check bug)."""
    command = split_profile(fix.argv)[1]
    first = command[0] if command else ""
    name = builtin_for(first) if first else None
    if name is None:
        return UntapedError(not_found("command", first))
    if name == "doctor":
        return UntapedError("a fix cannot run doctor")
    return None


def _line(fix: _Fix, profile: str) -> str:
    return run_line(list(fix.argv), profile)


def _run_one_fix(fix: _Fix) -> ChildRun:
    try:
        return _run_one(fix.argv)
    except OSError as exc:
        raise UntapedError(f"could not start untaped: {exc}") from exc


def _run_one(argv: Sequence[str]) -> ChildRun:
    """Run ``untaped <argv>`` as a child with stdin closed and JSON output; never raises on exit.

    ``--format json`` is appended unless ``argv`` names a format; the child
    gets JSON stderr diagnostics and no ``UNTAPED_FORMAT``. Under
    ``--verbose`` both streams are relayed to stderr as they arrive.
    """
    command = [sys.executable, "-m", "untaped", *argv]
    if not any(arg in _FORMAT_FLAGS or arg.startswith("--format=") for arg in argv):
        command += ["--format", "json"]
    env = {name: value for name, value in os.environ.items() if name != "UNTAPED_FORMAT"}
    env[DIAGNOSTICS_ENV] = "json"
    relay = is_verbose()
    out: list[str] = []
    err: list[str] = []
    with subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as process:
        readers = [
            threading.Thread(target=_drain, args=(process.stdout, out, relay), daemon=True),
            threading.Thread(target=_drain, args=(process.stderr, err, relay), daemon=True),
        ]
        for reader in readers:
            reader.start()
        code = process.wait()
        for reader in readers:
            reader.join()
    return ChildRun(code=code, rows=_parse_rows("".join(out)), diagnostics=_parse_lines(err))


def _drain(stream: IO[str] | None, into: list[str], relay: bool) -> None:
    for line in stream or ():
        into.append(line)
        if relay:
            current_sink().passthrough(line)


def _parse_rows(text: str) -> list[Row]:
    """The child's outcome rows: a JSON array or one object; anything else is none."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    items = data if isinstance(data, list) else [data]
    return [item for item in items if isinstance(item, dict)]


def _parse_lines(lines: list[str]) -> list[Row]:
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _outcome(
    fix: _Fix,
    refusal: UntapedError | None,
    run: ChildRun | UntapedError | None,
    after: list[dict[str, object]],
    *,
    dry_run: bool,
) -> FixOutcome:
    outcome = partial(FixOutcome, fix=list(fix.argv), checks=list(fix.checks))
    if refusal is not None:
        info = Failure.from_exception(refusal)
        return outcome(action="failed", detail=_error_detail(info), error=info)
    if not fix.automatic:
        return outcome(action="skipped", detail=f"{_needs(fix)}; run it yourself")
    if dry_run:
        return outcome(action="planned", detail=f"would fix {', '.join(fix.checks)}")
    if not isinstance(run, ChildRun):
        info = Failure.from_exception(run or UntapedError("did not run"))
        return outcome(action="failed", detail=_error_detail(info), error=info)
    if run.code != 0:
        info = _child_error(run)
        return outcome(action="failed", detail=_error_detail(info), error=info)
    detail = _done_detail(run)
    remaining = [r for r in after if _key(r) in fix.keys and r["status"] != "pass"]
    if not remaining:
        return outcome(action="fixed", detail=detail)
    first = remaining[0]
    still = "still fails" if first["status"] == "fail" else "still warns"
    return outcome(action="partial", detail=f"{detail}; {still}: {first['detail']}")


def _needs(fix: _Fix) -> str:
    found = list(dict.fromkeys(placeholders(list(fix.argv))))
    return f"needs {', '.join(found)}" if found else "asks for input"


def _done_detail(run: ChildRun) -> str:
    """The child's outcome ``action`` counts; else its last ``success`` line; else ``done``."""
    counts: dict[str, int] = {}
    for item in run.rows:
        action = item.get("action")
        if isinstance(action, str) and action:
            counts[action] = counts.get(action, 0) + 1
    if counts:
        return ", ".join(f"{count} {action}" for action, count in counts.items())
    messages = [
        str(record.get("message"))
        for record in run.diagnostics
        if record.get("level") == "success" and record.get("message")
    ]
    return messages[-1] if messages else "done"


def _child_error(run: ChildRun) -> Failure:
    """The child's last JSON error line as a :class:`Failure`, else a generic failure."""
    for record in reversed(run.diagnostics):
        if record.get("level") != "error":
            continue
        try:
            return Failure.model_validate(
                {name: record[name] for name in Failure.model_fields if name in record}
            )
        except ValidationError:
            break
    return Failure(
        category=ErrorCategory.FAILED,
        system="untaped",
        retryable=False,
        message=f"exited with status {run.code}",
    )


def _error_detail(info: Failure) -> str:
    return f"{info.message}; {info.hint}" if info.hint else info.message


def _emit(
    rows: list[FixOutcome], *, fmt: OutputFormat, columns: list[str] | None, profile: str
) -> None:
    count_row_failures(rows)  # a failed fix's ``error`` sets the exit code
    if fmt == "table" and columns is None:
        if rows:
            emit_fix_list(rows, run_line=lambda argv: run_line(argv, profile))
        return
    if fmt == "table":
        # A table shows ``fix`` as the line to type, so it is a view, not the record.
        shown = [row.model_dump(mode="json") | {"fix": run_line(row.fix, profile)} for row in rows]
        emit_isolated(shown, fmt=fmt, columns=columns)
    else:
        emit_isolated(rows, fmt=fmt, columns=columns)


def _exit_unhealthy(rows: list[dict[str, object]], profile: str) -> None:
    """Exit 1, pointing at ``doctor`` for ``profile``, when a check still fails."""
    if any(row["status"] == "fail" for row in rows):
        _diag.hint(run_hint(run_line(command_argv("doctor", profile=profile), profile)))
        raise SystemExit(ExitCode.FAILURE)


__all__ = ["ChildRun", "run_fixes"]
