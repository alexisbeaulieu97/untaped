"""Test helpers for plugins with rules, and for plugins checked by lint's conventions.

- :func:`assert_clean` fails where ``untaped lint`` would exit non-zero for
  an installed plugin: what each plugin's convention test calls.
- :func:`subject` builds a :class:`~untaped_lint.records.Subject` from a record.
- :func:`run` lints subjects and returns the report's findings and failures.
- :func:`conformance` checks a ``Linter`` provider's rule table.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from importlib import metadata
from pathlib import Path

from untaped.sdk import Failure, Record, installed_plugins, kind_of
from untaped.testing import PluginCandidate
from untaped_lint.application.engine import Options
from untaped_lint.application.engine import run as _run
from untaped_lint.application.rules import Linter
from untaped_lint.application.subjects import plugin_subject
from untaped_lint.domain.report import Report
from untaped_lint.domain.selection import parse_rule, parse_suppression
from untaped_lint.providers.conventions import KEY
from untaped_lint.records import RULE_ID, Finding, Subject

__all__ = ["assert_clean", "conformance", "run", "subject"]

_DISTRIBUTION = "untaped-lint"


def subject(record: Record) -> Subject:
    """``record`` as the subject a rule reading its kind receives."""
    kind = kind_of(type(record))
    if kind is None:
        raise TypeError(f"{type(record).__name__} declares no kind")
    return Subject(kind=kind, record=record.model_dump(mode="json"))


def run(
    subjects: Iterable[Subject],
    *,
    select: Iterable[str] = (),
    ignore: Iterable[str] = (),
    candidates: Sequence[PluginCandidate] | None = None,
) -> Report:
    """Lint ``subjects`` with the installed providers; nothing is printed."""
    options = Options(
        select=tuple(parse_rule(each, option="select") for each in select),
        ignore=tuple(parse_suppression(each, option="ignore") for each in ignore),
    )
    return _run(subjects, options, candidates=candidates)


def assert_clean(
    name: str,
    *,
    tests_dir: Path | None = None,
    ignore: Iterable[str] = (),
    strict: bool = False,
    candidates: Sequence[PluginCandidate] | None = None,
) -> None:
    """Fail where ``untaped lint`` would exit non-zero for plugin ``name``, listing every finding.

    That is an error finding or a failure row, or any finding with
    ``strict``, as on the command line. ``tests_dir`` adds the
    private-test-import rule over those tests (a checkout's ``tests/`` is
    found on its own). ``candidates`` replaces entry-point discovery, so an
    uninstalled spec can be checked.
    """
    from untaped.testing import compose_with  # noqa: PLC0415 - composes only when called

    if candidates is not None:
        candidates = _with_lint(candidates)
    with compose_with(*(candidates or ())):
        plugin = next((each for each in installed_plugins() if each.name == name), None)
        if plugin is None:
            raise LookupError(f"no installed plugin named {name!r}")
        if tests_dir is not None:
            plugin = plugin.model_copy(update={"tests_dir": tests_dir.resolve()})
        report = run([plugin_subject(plugin)], ignore=ignore, candidates=candidates)
    problems = [_finding_text(each) for each in report.findings]
    problems += [_failure_text(each) for each in report.failures]
    if report.failures or report.predicate_hit(strict=strict):
        raise AssertionError(f"{name} breaks lint's rules:\n" + "\n".join(problems))


def _with_lint(candidates: Sequence[PluginCandidate]) -> list[PluginCandidate]:
    """``candidates`` plus lint itself, which declares the contract the run gathers."""
    if any(each.name == KEY for each in candidates):
        return list(candidates)
    lint = PluginCandidate(
        distribution=_DISTRIBUTION,
        name=KEY,
        target="untaped_lint:SPEC",
        distribution_version=metadata.version(_DISTRIBUTION),
        requires_dist=tuple(metadata.requires(_DISTRIBUTION) or ()),
    )
    return [*candidates, lint]


def conformance(provider: Linter) -> None:
    """Fail unless ``provider``'s rule table is one the engine loads: bare, unique ids."""
    rules = provider.rules()
    ids = [each.id for each in rules]
    duplicated = sorted({each for each in ids if ids.count(each) > 1})
    if duplicated:
        raise AssertionError(f"rule ids declared twice: {', '.join(duplicated)}")
    bad = [each for each in ids if not re.match(RULE_ID, each)]
    if bad:
        raise AssertionError(f"rule ids not bare kebab-case: {', '.join(bad)}")


def _finding_text(finding: Finding) -> str:
    where = getattr(finding.location, "path", None)
    prefix = f"{where}: " if where and where not in finding.message else ""
    return f"  {finding.rule} ({finding.severity}): {prefix}{finding.message}"


def _failure_text(failure: Failure) -> str:
    return f"  failure {failure.item}: {failure.message}"
