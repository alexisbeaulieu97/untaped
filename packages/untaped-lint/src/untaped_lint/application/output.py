"""A lint run's report from its subjects and selection.

:func:`lint_report` parses the selection and runs the engine; a command
prints its findings as rows and its failures and warnings on stderr.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from untaped_lint.config import LintConfig
from untaped_lint.domain.selection import parse_rule, parse_suppression
from untaped_lint.records import Finding, Subject

if TYPE_CHECKING:
    from untaped.sdk.testing import PluginCandidate
    from untaped_lint.domain.report import Report


def lint_report(
    subjects: Iterable[Subject],
    *,
    config: LintConfig = LintConfig(),  # noqa: B008 - frozen
    select: Iterable[str] = (),
    ignore: Iterable[str] = (),
    paths: Iterable[str] = (),
    only: Iterable[str] = (),
    exact: Iterable[str] = (),
    locations: Iterable[str] = (),
    findings: Iterable[Finding] = (),
    candidates: Sequence[PluginCandidate] | None = None,
) -> Report:
    """Lint ``subjects``: the report, its findings in the order a table shows them.

    ``only`` takes target globs and ``exact`` literal targets (a name with
    ``[`` or ``?`` is never read as a pattern); ``locations`` keeps only the
    findings at those locations, written as a table reads them
    (``job_templates/14``, ``templates.yml#2``), for a caller whose targets
    are not unique. ``findings`` are ones the caller already made, with
    public ids, reported and selected like the rest.
    """
    # The engine (contracts, providers, the plugin checks) loads when a run
    # starts, so a command that imports ``untaped_lint.api`` for its types
    # pays for none of it until it lints.
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
        locations=tuple(locations),
    )
    report = run(
        subjects,
        options,
        findings=findings,
        candidates=candidates,
        repo=repo,
        tree_root=config.tree_root,
    )
    report.findings.sort(key=_order)
    return report


def _order(finding: Finding) -> tuple[str, int, str, str]:
    location = finding.location
    path = getattr(location, "path", None) or getattr(location, "id", None) or ""
    line = getattr(location, "line", None) or getattr(location, "index", None) or 0
    return (path, line, finding.rule, finding.target)
