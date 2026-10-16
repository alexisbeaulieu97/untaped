"""``lint()``: run, print the findings and the failure rows, and exit by the rules.

Findings go to stdout (``lint.finding``); failure rows, warnings and the
summary to stderr. Exit 3 on an error finding (any finding with ``strict``),
1 on a failure row (or its category's code), 0 otherwise; usage mistakes
raise first (2).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, NoReturn

from untaped.sdk import Diagnostics, OutputFormat, emit, finish
from untaped_lint.config import LintConfig
from untaped_lint.domain.selection import parse_rule, parse_suppression
from untaped_lint.records import Finding, Subject, location_text

if TYPE_CHECKING:
    from untaped.testing import PluginCandidate

_diag = Diagnostics()


def lint(
    subjects: Iterable[Subject],
    *,
    config: LintConfig = LintConfig(),  # noqa: B008 - frozen
    select: Iterable[str] = (),
    ignore: Iterable[str] = (),
    paths: Iterable[str] = (),
    only: Iterable[str] = (),
    exact: Iterable[str] = (),
    strict: bool = False,
    findings: Iterable[Finding] = (),
    fmt: OutputFormat = "table",
    columns: list[str] | None = None,
    candidates: Sequence[PluginCandidate] | None = None,
) -> NoReturn:
    """Lint ``subjects``, print the rows and exit per the lint exit codes.

    ``only`` takes target globs and ``exact`` literal targets (a name with
    ``[`` or ``?`` is never read as a pattern). ``findings`` are ones the
    caller already made, with public ids, reported and selected like the rest.
    """
    # The engine (contracts, providers, the plugin checks) loads when a run
    # starts, so a command that imports ``untaped_lint.api`` for its option
    # types pays for none of it until it lints.
    from untaped_lint.application.engine import Options, run  # noqa: PLC0415

    repo = config.repo
    in_file = (
        ()
        if repo is None
        else tuple(parse_suppression(each, option=f"{repo.path} ignore:") for each in repo.ignore)
    )
    options = Options(
        select=tuple(parse_rule(each, option="--select") for each in select),
        ignore=(
            *(parse_suppression(each, option="lint.ignore") for each in config.ignore),
            *in_file,
            *(parse_suppression(each, option="--ignore") for each in ignore),
        ),
        paths=tuple(paths),
        only=tuple(only),
        exact=tuple(exact),
    )
    report = run(
        subjects,
        options,
        findings=findings,
        candidates=candidates,
        repo=repo,
        tree_root=config.tree_root,
    )
    for warning in report.warnings:
        _diag.warning(warning)
    for failure in report.failures:
        _diag.failure(failure)
    rows = sorted(report.findings, key=_order)
    if fmt == "table" and columns is None:
        emit(
            [_table_row(row) for row in rows],
            fmt=fmt,
            empty=False,
            table_columns=Finding.table_columns,
        )
    else:
        emit(rows, fmt=fmt, columns=columns, empty=False)
    _diag.info(report.summary())
    finish(bool(report.failures), predicate_hit=report.predicate_hit(strict=strict))
    raise SystemExit(0)


def _order(finding: Finding) -> tuple[str, int, str, str]:
    location = finding.location
    path = getattr(location, "path", None) or getattr(location, "id", None) or ""
    line = getattr(location, "line", None) or getattr(location, "index", None) or 0
    return (path, line, finding.rule, finding.target)


def _table_row(finding: Finding) -> dict[str, object]:
    row = finding.model_dump(mode="json")
    row["location"] = location_text(finding.location)
    if (
        row["location"] == finding.target
        or getattr(finding.location, "path", None) == finding.target
    ):
        row["target"] = ""
    return row
