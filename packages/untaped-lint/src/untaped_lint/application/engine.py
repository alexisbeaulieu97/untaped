"""One lint run: Conventions first, then every provider, answers turned into rows.

``untaped.plugin`` subjects go to :class:`Conventions` directly, before any
``gather``: it composes, which no contract method may. Then the providers'
rule tables are gathered once, ids prefixed with each provider's plugin, and
each subject is asked of the providers whose rules read its kind. A provider
that did not check never reads as a pass: each failure, invalid row, raise or
skip other than ``not-configured`` is one failure row.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from untaped.contracts import Failed, NoProviderReady, Ok, Skipped, gather
from untaped.sdk import ErrorCategory, Failure, Plugin
from untaped.testing import PluginCandidate
from untaped_lint.application.rules import Linter
from untaped_lint.application.subjects import PLUGIN_KIND
from untaped_lint.domain.report import Report
from untaped_lint.domain.selection import Resolver, Selection, Suppression
from untaped_lint.providers.conventions import KEY, Conventions
from untaped_lint.records import Finding, Rule, Subject

_RULES_HINT = "untaped lint rules"


@dataclass(frozen=True)
class Options:
    """What a run reports: the selection as typed, ``strict`` aside (the exit reads it)."""

    select: tuple[str, ...] = ()
    ignore: tuple[Suppression, ...] = ()
    paths: tuple[str, ...] = ()
    only: tuple[str, ...] = ()
    exact: tuple[str, ...] = ()


@dataclass
class _Providers:
    """The providers' rule tables, by plugin, and who reads each kind."""

    rules: dict[str, Rule] = field(default_factory=dict)
    """Every loaded rule by public id, Conventions' included."""
    declared: dict[str, set[str]] = field(default_factory=dict)
    """Each provider's bare ids."""
    readers: dict[str, list[str]] = field(default_factory=dict)


def loaded_rules(report: Report | None = None) -> dict[str, Rule]:
    """Every rule a run would load, by public id: Conventions' then each provider's."""
    return _load(report if report is not None else Report()).rules


def run(
    subjects: Iterable[Subject],
    options: Options = Options(),  # noqa: B008 - frozen
    *,
    findings: Iterable[Finding] = (),
    candidates: Sequence[PluginCandidate] | None = None,
) -> Report:
    """Lint ``subjects``; ``findings`` are ones already made (public ids), selected likewise.

    ``candidates`` replaces entry-point discovery for the plugin checks.
    """
    report = Report()
    subjects = list(subjects)
    raw = list(findings)
    for subject in subjects:
        if subject.kind == PLUGIN_KIND:
            plugin = Plugin.model_validate(subject.record)
            raw.extend(
                _public(KEY, each) for each in Conventions().findings(plugin, candidates=candidates)
            )
    providers = _load(report)
    unread: set[str] = set()
    for subject in subjects:
        readers = providers.readers.get(subject.kind, [])
        if readers:
            raw.extend(_ask(subject, readers, providers, report))
        elif subject.kind != PLUGIN_KIND and subject.kind not in unread:
            unread.add(subject.kind)
            report.warnings.append(
                f"no installed rule reads {subject.kind} records; see `{_RULES_HINT}`"
            )
    resolver = Resolver(providers.rules)
    selection = Selection.build(
        resolver,
        select=options.select,
        ignore=options.ignore,
        paths=options.paths,
        only=options.only,
        exact=options.exact,
    )
    report.warnings.extend(resolver.warnings)
    report.findings.extend(each for each in raw if selection.keeps(each))
    return report


def _load(report: Report) -> _Providers:
    providers = _Providers(rules={f"{KEY}.{rule.id}": rule for rule in Conventions().rules()})
    try:
        answers = gather(Linter.rules)()
    except NoProviderReady as exc:
        for plugin, ready in sorted(exc.not_ready.items()):
            report.warnings.append(f"{plugin}'s rules did not run: {ready.reason}")
        for plugin, reason in sorted(exc.quarantined.items()):
            report.failures.append(_failure(plugin, "quarantined", reason, ErrorCategory.CONFIG))
        return providers
    for answer in answers:
        if isinstance(answer, Ok):
            _declare(answer.plugin, answer.value, providers, report)
        else:
            _not_checked(answer, report, rules=None)
    return providers


def _declare(plugin: str, rules: list[Rule], providers: _Providers, report: Report) -> None:
    ids = {rule.id for rule in rules}
    if plugin == KEY:
        report.failures.append(
            _failure(plugin, "reserved-key", f"{KEY} is lint's own key; its rules were not loaded")
        )
        return
    providers.declared[plugin] = ids
    for each in rules:
        providers.rules[f"{plugin}.{each.id}"] = each
        readers = providers.readers.setdefault(each.reads, [])
        if plugin not in readers:
            readers.append(plugin)


def _ask(
    subject: Subject, readers: list[str], providers: _Providers, report: Report
) -> list[Finding]:
    found: list[Finding] = []
    for answer in gather(Linter.findings, plugins=readers)(subject):
        if not isinstance(answer, Ok):
            _not_checked(answer, report, rules=len(providers.declared.get(answer.plugin, ())))
            continue
        declared = providers.declared.get(answer.plugin, set())
        for row in answer.invalid:
            report.failures.append(
                _failure(answer.plugin, "invalid-item", row, details={"rules": 1})
            )
        for each in answer.value:
            public = f"{answer.plugin}.{each.rule}"
            if each.rule not in declared:
                report.failures.append(
                    _failure(
                        answer.plugin,
                        "undeclared-rule",
                        f"a finding names {each.rule!r}, which its rules() does not declare",
                    )
                )
            elif each.error is not None:
                report.failures.append(
                    each.error.model_copy(
                        update={
                            "item": public,
                            "hint": f"untaped lint --select {public}",
                            "details": {**each.error.details, "reason": "raised", "rules": 1},
                        }
                    )
                )
            else:
                found.append(_public(answer.plugin, each))
    return found


def _not_checked(answer: Failed | Skipped, report: Report, *, rules: int | None) -> None:
    details = {} if rules is None else {"rules": rules}
    if isinstance(answer, Failed):
        failure = Failure.from_exception(answer.error)
        report.failures.append(
            failure.model_copy(
                update={
                    "item": answer.plugin,
                    "hint": failure.hint or _RULES_HINT,
                    "details": {**failure.details, "reason": answer.reason or "failed", **details},
                }
            )
        )
    elif answer.reason == "not-configured":
        report.warnings.append(f"{answer.plugin}'s rules did not run: {answer.detail}")
    else:
        report.failures.append(
            _failure(answer.plugin, answer.reason, answer.detail, details=details)
        )


def _failure(
    plugin: str,
    reason: str,
    detail: str,
    category: ErrorCategory = ErrorCategory.FAILED,
    *,
    details: dict[str, int] | None = None,
) -> Failure:
    return Failure(
        category=category,
        system=plugin,
        item=plugin,
        message=f"{plugin}'s rules did not run: {reason}: {detail}",
        hint=_RULES_HINT,
        details={"reason": reason, **(details or {})},
    )


def _public(key: str, finding: Finding) -> Finding:
    return finding.model_copy(update={"rule": f"{key}.{finding.rule}"})
