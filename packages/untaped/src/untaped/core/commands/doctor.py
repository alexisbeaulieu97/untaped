"""Root ``untaped doctor`` command group: ``Doctor`` on core's plugin class.

``untaped doctor`` (``Doctor.runs``, :class:`Check`) runs core's plus
every composed plugin's health checks OFFLINE — config-file reads plus
in-process model validation only, never network I/O. ``--online`` adds the checks
plugins contribute with ``DoctorCheck(online=True)``, which contact the
configured services. A check's ``DoctorResult.fix`` becomes the row's
``fix``: a complete argv (``--profile`` first) to run after ``untaped``;
the human view shows it under its row as the command line to type. A row's
``automatic`` says the fix is safe to run unattended (see ``DoctorResult``).
Each row is isolated: invalid settings for one plugin surface as failed
rows while every other row still runs.
Quarantine records render as failed rows (nonzero exit). Core's own checks
(config file permissions, unknown keys, the ``pass`` store) are
``DoctorCheck`` attributes on ``Core`` (:mod:`untaped.core.doctor_checks`),
run like any plugin's. A ``warn`` row does not fail the run: a config file
other users can read, profile keys no settings model declares, renamed,
retired, deleted or deprecated keys
in any profile (fixed by ``config migrate``, except deprecated settings),
installed skills an older untaped wrote, someone edited or no plugin ships,
and a plugin check that returns ``DoctorResult(..., warn=True)``. ``doctor
fix`` (:class:`Fix`, driving :mod:`untaped.core.commands.fix`) runs every
automatic fix the checks name. The rows themselves come from
:mod:`untaped.core.doctor_rows`.
"""

from __future__ import annotations

import shlex
from collections.abc import Generator, Iterator
from typing import Any

from pydantic import Field

from untaped.core.commands import records
from untaped.core.commands._render import emit_check_list, emit_isolated
from untaped.core.doctor_row import FAIL, PASS, STATUSES
from untaped.core.doctor_rows import collect_doctor_rows
from untaped.core.settings import CoreSettings
from untaped.runtime.profile_resolver import (
    DEFAULT_PROFILE,
    flag_pick,
    picked_names,
    selected_profile,
)
from untaped.runtime.run_output import Diagnostics
from untaped.sdk._confirm import Confirm
from untaped.sdk._output import Output
from untaped.sdk._table import table_row
from untaped.sdk.errors import Failed
from untaped.sdk.plugin import Command, Group
from untaped.sdk.words import (
    command_argv,
    command_line,
    plural,
    run_hint,
    split_profile,
    summary,
)

_diag = Diagnostics()


_ONLINE = "Also contact each configured service (tokens, URLs, TLS)."


class Check(Command[CoreSettings]):
    """Run every health check and report one row per check.

    Examples:
        $ untaped doctor
        $ untaped doctor --online --format json
    """

    repairs = True
    online: bool = Field(False, description=_ONLINE)
    output: Output[records.DoctorCheck]

    def run(self) -> Iterator[records.DoctorCheck]:
        # Lazy: bootstrap is the CLI's root and imports this module.
        from untaped.runtime.bootstrap import composition  # noqa: PLC0415

        rows = collect_doctor_rows(composition(), online=self.online)
        if (yield from report_check_rows(rows, op="doctor", output=self.output)):
            raise Failed("a doctor check failed")


class Fix(Command[CoreSettings]):
    """Run every automatic fix doctor found, after one confirmation.

    No automatic fix asks for or prints a token, so `--yes` passes none.
    A keychain may show an unlock prompt on the user's screen; a locked
    `pass` store exits 5 with a hint naming the unlock command. A `skipped`
    row's `fix` is the argv to run after `untaped`: a `<NAME>` token in it
    is a value to ask for, and a fix that asks for or reveals a token
    (`auth login`) is the user's to run.

    Examples:
        $ untaped doctor fix --dry-run
        $ untaped doctor fix --yes
    """

    repairs = True
    online: bool = Field(False, description=_ONLINE)
    confirm: Confirm
    output: Output[records.FixOutcome]

    def run(self) -> Iterator[records.FixOutcome]:
        # Lazy: bootstrap is the CLI's root and imports this module; fix imports doctor.
        from untaped.core.commands.fix import run_fixes  # noqa: PLC0415
        from untaped.runtime.bootstrap import composition, root_command  # noqa: PLC0415

        return run_fixes(
            composition(),
            online=self.online,
            confirm=self.confirm,
            shows_itself=_shows_itself(self.output),
            columns=self.output.requested,
            builtin_for=root_command,
        )


class Doctor(Group):
    """Check the health of untaped and every composed plugin."""

    runs = Check
    commands = (Fix,)


def _shows_itself(output: Output[Any]) -> bool:
    """Whether the command prints its own human view: a ``table`` not listing its columns.

    Rows print only after ``run`` ends, and the human view puts the summary
    and hint after them, so doctor draws its table itself (with
    ``--columns``, a table whose ``fix`` is the line to type).
    """
    return output.format == "table" and output.requested != ["?"]


def report_check_rows(
    rows: list[dict[str, object]], *, op: str, output: Output[Any]
) -> Generator[records.DoctorCheck, None, bool]:
    """Report doctor rows and an ``op`` footer; return whether a check failed.

    Under a ``table`` the command prints the checklist itself (with
    ``--columns``, the table whose ``fix`` is the command line to type);
    otherwise it yields the ``untaped.doctor_check`` records for ``output``.
    """
    if _shows_itself(output):
        _show_check_rows(rows, columns=output.requested)
    else:
        yield from (records.DoctorCheck.model_validate(row) for row in rows)
    return _check_footer(rows, op=op)


def _show_check_rows(rows: list[dict[str, object]], *, columns: list[str] | None) -> None:
    """Doctor rows as a person reads them: the checklist, or with ``columns`` a table."""
    if columns is None:
        emit_check_list(rows, run_line=run_line)
        _diag.info("")
    else:
        # A table shows ``fix`` as the line to type, so it is a view, not the record.
        shown = [
            table_row(records.DoctorCheck.model_validate(row), _table_row(row)) for row in rows
        ]
        emit_isolated(shown, fmt="table", columns=columns)


def _check_footer(rows: list[dict[str, object]], *, op: str) -> bool:
    """Print the ``op`` summary and the ``doctor fix`` hint; return whether a check failed."""
    counts = {status: sum(row["status"] == status for row in rows) for status in STATUSES}
    _diag.info(summary(op, counts))
    fix_hint = _fix_hint(rows)
    if fix_hint:
        _diag.hint(fix_hint)
    return bool(counts[FAIL])


def _fix_hint(rows: list[dict[str, object]]) -> str | None:
    """``run `untaped doctor fix` …`` (a hint's body) when an automatic fix exists, else ``None``.

    Fixes are counted as ``doctor fix`` groups them; the command reads the
    run's whole pick, as doctor did.
    """
    fixes = {argv: is_automatic(covered) for argv, covered in group_fixes(rows).items()}
    automatic = [argv for argv, auto in fixes.items() if auto]
    if not automatic:
        return None
    command = run_line(command_argv(Fix.spelled(), profile=selected_profile()))
    text = (
        f"{run_hint(command)} to apply {plural(len(automatic), 'automatic fix', 'automatic fixes')}"
    )
    manual = len(fixes) - len(automatic)
    if manual:
        text = f"{text}; {plural(manual, 'fix', 'fixes')} {'needs' if manual == 1 else 'need'} you"
    return text


def _table_row(row: dict[str, object]) -> dict[str, object]:
    fix = row.get("fix")
    return {**row, "fix": run_line(fix) if isinstance(fix, list) and fix else ""}


def run_line(argv: list[str]) -> str:
    """A fix or step argv as the ``untaped …`` line a person types.

    ``--profile default`` goes without saying when the run picks no profile
    and no ``--profile`` was given: the bare line then reads and writes the
    base too.
    """
    named, rest = split_profile(argv)
    if named == DEFAULT_PROFILE and not flag_pick() and not picked_names():
        argv = rest
    return command_line(shlex.join(argv))


def group_fixes(rows: list[dict[str, object]]) -> dict[tuple[str, ...], list[dict[str, object]]]:
    """Warned and failed rows with a fix, keyed by exact fix argv, first appearance first."""
    grouped: dict[tuple[str, ...], list[dict[str, object]]] = {}
    for row in rows:
        fix = row.get("fix")
        if row["status"] != PASS and isinstance(fix, list) and fix:
            grouped.setdefault(tuple(str(arg) for arg in fix), []).append(row)
    return grouped


def is_automatic(covered: list[dict[str, object]]) -> bool:
    """A grouped fix is automatic only when every row naming it says so."""
    return all(row.get("automatic") for row in covered)


__all__ = ["Doctor", "report_check_rows"]
