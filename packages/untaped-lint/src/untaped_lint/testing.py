"""Test helpers for plugins with rules, and for plugins checked by lint's conventions.

- :func:`assert_clean` fails where ``untaped lint`` would exit non-zero for
  an installed plugin: what each plugin's convention test calls.
- :func:`subject` builds a :class:`~untaped_lint.records.Subject` from a record,
  :func:`tree` one from a few files.
- :func:`run` lints subjects and returns the report's findings and failures;
  :func:`run_rule` runs one rule on one subject.
- :func:`repo_rule` loads a rule of the repository's ``.untaped/lint.yml``.
- :func:`conformance` checks a ``Linter`` provider's rule table.
"""

from __future__ import annotations

import atexit
import re
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from importlib import metadata
from pathlib import Path

from untaped.sdk import Failure, Record, installed_plugins, kind_of, record_model
from untaped.sdk.testing import PluginCandidate
from untaped_lint.application import repo_file
from untaped_lint.application.engine import Options
from untaped_lint.application.engine import run as _run
from untaped_lint.application.modules import load
from untaped_lint.application.ports import RuleFunction
from untaped_lint.application.rules import Linter, TreeReader
from untaped_lint.application.subjects import plugin_subject
from untaped_lint.domain.report import Report
from untaped_lint.domain.selection import parse_rule, parse_suppression
from untaped_lint.providers.conventions import KEY
from untaped_lint.records import RULE_ID, Finding, Subject, Tree

__all__ = ["assert_clean", "conformance", "repo_rule", "run", "run_rule", "subject", "tree"]

_DISTRIBUTION = "untaped-lint"


def subject(record: Record) -> Subject:
    """``record`` as the subject a rule reading its kind receives."""
    kind = kind_of(type(record))
    if kind is None:
        raise TypeError(f"{type(record).__name__} declares no kind")
    return Subject(kind=kind, record=record.model_dump(mode="json"))


def tree(files: Mapping[str, str], *, root: Path | None = None) -> Subject:
    """A ``Tree`` subject holding ``files`` (path to text), written under ``root``.

    ``root`` defaults to a new temporary directory, removed at exit; a ``bytes`` text
    can't be given, so write an undecodable file yourself and list it.
    """
    if root is None:
        base = Path(tempfile.mkdtemp(prefix="untaped-lint-"))
        atexit.register(shutil.rmtree, base, ignore_errors=True)
    else:
        base = root
    for path, text in files.items():
        target = base / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return subject(Tree(root=base.resolve(), paths=tuple(sorted(files))))


def run_rule(rule: RuleFunction, subject: Subject) -> list[Finding]:
    """The findings ``rule`` yields for ``subject``, ids bare; a raise propagates.

    Unlike :func:`run`, no provider, selection or ``# untaped: allow``
    marker is involved: the rule alone, as its test needs.
    """
    if subject.kind != rule.rule.reads:
        raise TypeError(f"{rule.rule.id} reads {rule.rule.reads}, not {subject.kind}")
    return list(rule(rule.model.model_validate(subject.record)))


def repo_rule(rule_id: str, *, root: Path) -> RuleFunction:
    """Rule ``rule_id`` (bare) of the ``.untaped/lint.yml`` found from ``root``, loaded."""
    path = repo_file.find(root)
    if path is None:
        raise LookupError(f"no {repo_file.RELATIVE} at or above {root}")
    loaded = load(repo_file.read(path))
    for host in loaded.hosts.values():
        for function in host.RULES:
            if function.rule.id == rule_id:
                return function
    problems = "; ".join(failure.message for failure in loaded.failures)
    raise LookupError(f"no rule {rule_id!r} in {path}" + (f" ({problems})" if problems else ""))


def run(
    subjects: Iterable[Subject] = (),
    *,
    select: Iterable[str] = (),
    ignore: Iterable[str] = (),
    candidates: Sequence[PluginCandidate] | None = None,
    root: Path | None = None,
) -> Report:
    """Lint ``subjects`` with the installed providers; nothing is printed.

    ``root`` also lints that directory as ``untaped lint`` run there would:
    its ``Tree`` and the ``.untaped/lint.yml`` found from it (rules, readers,
    ignores); the profile's ``lint.ignore`` is not read.
    """
    repo = None
    if root is not None and (path := repo_file.find(root)) is not None:
        repo = repo_file.read(path)
    file_ignore = repo.ignore if repo is not None else ()
    options = Options(
        select=tuple(parse_rule(each, option="select") for each in select),
        ignore=tuple(parse_suppression(each, option="ignore") for each in (*file_ignore, *ignore)),
    )
    tree_root = None if root is None else (repo.root if repo is not None else root)
    return _run(subjects, options, candidates=candidates, repo=repo, tree_root=tree_root)


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
    from untaped.sdk.testing import compose_with  # noqa: PLC0415 - composes only when called

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


def conformance(provider: Linter | TreeReader) -> None:
    """Fail unless ``provider``'s rule table is one the engine loads: bare, unique ids.

    untaped's plugin checks call it for every provider of lint's contracts;
    a ``TreeReader`` has no rule table, so it passes as it is.
    """
    if not isinstance(provider, Linter):
        return
    rules = provider.rules()
    ids = [each.id for each in rules]
    duplicated = sorted({each for each in ids if ids.count(each) > 1})
    if duplicated:
        raise AssertionError(f"rule ids declared twice: {', '.join(duplicated)}")
    bad = [each for each in ids if not re.match(RULE_ID, each)]
    if bad:
        raise AssertionError(f"rule ids not bare kebab-case: {', '.join(bad)}")
    unknown = sorted({each.reads for each in rules if record_model(each.reads) is None})
    if unknown:
        raise AssertionError(f"rules read kinds no model declares: {', '.join(unknown)}")


def _finding_text(finding: Finding) -> str:
    where = getattr(finding.location, "path", None)
    prefix = f"{where}: " if where and where not in finding.message else ""
    return f"  {finding.rule} ({finding.severity}): {prefix}{finding.message}"


def _failure_text(failure: Failure) -> str:
    return f"  failure {failure.item}: {failure.message}"
