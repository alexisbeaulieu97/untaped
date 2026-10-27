"""One lint run: Conventions first, then every provider, answers turned into rows.

``untaped.installed_plugin`` subjects go to :class:`Conventions` directly, before any
``gather``: it composes, which no contract method may. Then the repository's
own rule modules (``.untaped/lint.yml``) are composed as one plugin per key,
the providers' rule tables are gathered once, ids prefixed with each
provider's plugin, the ``readers:`` turn files into subjects, and each
subject is asked of the providers whose rules read its kind; the
repository's ``Tree`` is built only when a rule reads it. A provider that did
not check never reads as a pass: each failure, invalid row, raise or skip
other than ``not-configured`` is one failure row.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from untaped.sdk import ConfigError, ErrorCategory, Failure, InstalledPlugin, plural
from untaped.sdk.contracts import (
    Errored,
    NoProviderReady,
    Ok,
    Reason,
    Skipped,
    Source,
    gather,
    providing,
)
from untaped.sdk.testing import PluginCandidate
from untaped_lint.application.modules import RULE_IMPORTS, TREE_KIND, Loaded, load
from untaped_lint.application.repo_file import RepoFile
from untaped_lint.application.rules import Linter, TreeReader
from untaped_lint.application.subjects import PLUGIN_KIND
from untaped_lint.application.tree_paths import tree_paths
from untaped_lint.domain.allow import allowed_names
from untaped_lint.domain.glob import matches
from untaped_lint.domain.report import Report
from untaped_lint.domain.selection import Resolver, Selection, Suppression
from untaped_lint.providers.conventions import (
    KEY,
    UNCHECKED,
    UNREADABLE,
    Conventions,
    installed,
)
from untaped_lint.records import (
    FileLocation,
    Finding,
    Rule,
    Subject,
    Tree,
    declared_kind,
    public,
    unreadable_files,
)

_RULES_HINT = "untaped lint rules"
_NOTHING = (
    "nothing to lint here: run it in a plugin repository, a repository with "
    ".untaped/lint.yml, or pipe records with --stdin"
)
_NO_TREE_RULE = (
    "nothing to lint here: no installed rule reads the repository's files; "
    "write one with `untaped lint new`"
)
_RULE_KIND = declared_kind(Rule)


@dataclass(frozen=True)
class Options:
    """What a run reports: the selection as typed, ``strict`` aside (the exit reads it)."""

    select: tuple[str, ...] = ()
    ignore: tuple[Suppression, ...] = ()
    paths: tuple[str, ...] = ()
    only: tuple[str, ...] = ()
    exact: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()


@dataclass
class _Providers:
    """The providers' rule tables, by plugin, and who reads each kind."""

    rules: dict[str, Rule] = field(default_factory=dict)
    """Every loaded rule by public id, Conventions' included."""
    declared: dict[str, set[str]] = field(default_factory=dict)
    """Each provider's bare ids."""
    readers: dict[str, list[str]] = field(default_factory=dict)


def loaded_rules(
    report: Report | None = None,
    *,
    repo: RepoFile | None = None,
    candidates: Sequence[PluginCandidate] | None = None,
) -> dict[str, Rule]:
    """Every rule a run would load, by public id: lint's own, the repository's, each provider's."""
    report = report if report is not None else Report()
    with _composed(repo, report, candidates) as (providers, _):
        return providers.rules


def run(
    subjects: Iterable[Subject],
    options: Options = Options(),  # noqa: B008 - frozen
    *,
    findings: Iterable[Finding] = (),
    candidates: Sequence[PluginCandidate] | None = None,
    repo: RepoFile | None = None,
    tree_root: Path | None = None,
) -> Report:
    """Lint ``subjects``; ``findings`` are ones already made (public ids), selected likewise.

    ``candidates`` replaces entry-point discovery. ``repo`` is the
    repository's lint file, if any; ``tree_root`` is where the ``Tree`` (and
    the readers' files) come from, when the run lints a directory.
    """
    report = Report()
    subjects = list(subjects)
    raw = [*findings, *_conventions(subjects, report, candidates)]
    marked: list[tuple[Finding, Path]] = []
    with _composed(repo, report, candidates) as (providers, loaded):
        if repo is not None:
            marked.extend((public(KEY, each), repo.root) for each in loaded.findings)
        tree = None
        if tree_root is not None and (TREE_KIND in providers.readers or (repo and repo.readers)):
            tree = _tree(tree_root, repo)
        with unreadable_files() as skipped:
            if tree is not None and repo is not None:
                subjects.extend(_read(tree, repo, report))
            raw.extend(_dispatch(subjects, providers, report))
            if tree is not None and TREE_KIND in providers.readers:
                asked = _ask(_subject(tree), providers.readers[TREE_KIND], providers, report)
                marked.extend((each, Path(tree.root)) for each in asked)
        if tree is not None:
            marked.extend((_unreadable(path), Path(tree.root)) for path in sorted(skipped))
    if not subjects and tree is None and not raw and not marked:
        report.warnings.append(_NOTHING if repo is None else _NO_TREE_RULE)
    resolver = Resolver(providers.rules)
    raw.extend(each for each, root in marked if not _allowed(each, root, resolver))
    selection = Selection.build(
        resolver,
        select=options.select,
        ignore=options.ignore,
        paths=options.paths,
        only=options.only,
        exact=options.exact,
        locations=options.locations,
        unchecked=UNCHECKED,
    )
    report.warnings.extend(resolver.warnings)
    report.findings.extend(each for each in raw if selection.keeps(each))
    return report


def _conventions(
    subjects: list[Subject], report: Report, candidates: Sequence[PluginCandidate] | None
) -> list[Finding]:
    """Conventions' findings for each ``untaped.installed_plugin`` subject, before gathering."""
    found: list[Finding] = []
    plugins: list[InstalledPlugin] | None = None
    for subject in subjects:
        if subject.kind != PLUGIN_KIND:
            continue
        try:
            plugin = InstalledPlugin.model_validate(subject.record)
        except ValidationError as exc:
            report.failures.append(_invalid_plugin(subject, exc))
            continue
        if plugins is None:
            plugins = installed(candidates)
        rows = Conventions().findings(plugin, candidates=candidates, plugins=plugins)
        found.extend(public(KEY, each) for each in rows)
    return found


def _dispatch(subjects: list[Subject], providers: _Providers, report: Report) -> list[Finding]:
    """Each subject asked of the providers reading its kind; a kind nobody reads warns once."""
    found: list[Finding] = []
    unread: set[str] = set()
    for subject in subjects:
        readers = providers.readers.get(subject.kind, [])
        if readers:
            found.extend(_ask(subject, readers, providers, report))
        elif subject.kind != PLUGIN_KIND and subject.kind not in unread:
            unread.add(subject.kind)
            report.warnings.append(
                f"no installed rule reads {subject.kind} records; see `{_RULES_HINT}`"
            )
    return found


@contextmanager
def _composed(
    repo: RepoFile | None, report: Report, candidates: Sequence[PluginCandidate] | None
) -> Iterator[tuple[_Providers, Loaded]]:
    """The repository's rule hosts composed beside the installed providers, rules loaded."""
    loaded = load(repo) if repo is not None else Loaded()
    report.failures.extend(loaded.failures)
    offered = {key: [host] for key, host in loaded.hosts.items()}
    composing = providing(KEY, offered, candidates=candidates) if offered else nullcontext()
    with composing:
        yield _load(report), loaded


def _tree(root: Path, repo: RepoFile | None) -> Tree:
    left_out = repo.left_out() if repo is not None else ()
    paths = tuple(
        path for path in tree_paths(root) if not any(matches(pattern, path) for pattern in left_out)
    )
    return Tree(root=root.resolve(), paths=paths)


def _subject(record: Tree) -> Subject:
    return Subject(kind=TREE_KIND, record=record.model_dump(mode="json"))


def _read(tree: Tree, repo: RepoFile, report: Report) -> list[Subject]:
    """The subjects each ``readers:`` entry reads from the files its globs match."""
    found: list[Subject] = []
    for key, globs in repo.readers.items():
        narrowed = tree.model_copy(
            update={"paths": tuple(p for p in tree.paths if any(matches(g, p) for g in globs))}
        )
        try:
            answers = gather(TreeReader.read, plugins=[key])(narrowed)
        except NoProviderReady:
            raise ConfigError(
                f"{repo.path}: readers.{key}: no plugin {key!r} reads files into subjects; "
                f"install untaped[{key}], or name a plugin that fills TreeReader "
                f"(`untaped plugin list` shows what is installed)"
            ) from None
        for answer in answers:
            if not isinstance(answer, Ok):
                _not_checked(answer, report, rules=None)
                continue
            for row in answer.invalid:
                report.failures.append(_failure(answer.plugin, "invalid-item", row))
            found.extend(answer.value)
    return found


def _unreadable(path: str) -> Finding:
    return public(
        KEY,
        Finding(
            rule=UNREADABLE.id,
            severity=UNREADABLE.severity,
            target=path,
            message="this file is not UTF-8, so no rule read it",
            location=FileLocation(path=path),
        ),
    )


def _allowed(finding: Finding, root: Path, resolver: Resolver) -> bool:
    """Whether the source line of ``finding`` carries ``# untaped: allow`` for its rule."""
    location = finding.location
    if not isinstance(location, FileLocation) or location.line is None:
        return False
    try:
        lines = (root / location.path).read_text(encoding="utf-8").splitlines()
    except OSError, UnicodeDecodeError:
        return False
    if not 1 <= location.line <= len(lines):
        return False
    names = allowed_names(lines[location.line - 1])
    return any(finding.rule in resolver.resolve(name) for name in sorted(names))


def _load(report: Report) -> _Providers:
    own = Source(plugin=KEY, kind=_RULE_KIND)
    providers = _Providers(
        rules={
            f"{KEY}.{rule.id}": rule.model_copy(update={"source": own})
            for rule in (*Conventions().rules(), RULE_IMPORTS)
        }
    )
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
            for row in answer.invalid:  # a malformed rule is dropped, its plugin's others kept
                report.failures.append(
                    _failure(answer.plugin, "invalid-item", row, lead="malformed rule was dropped")
                )
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
    if len(ids) < len(rules):
        report.failures.append(
            _failure(plugin, "duplicate-id", "two of its rules share an id", ErrorCategory.INVALID)
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
            public_id = f"{answer.plugin}.{each.rule}"
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
                            "item": public_id,
                            "hint": f"untaped lint --select {public_id}",
                            "details": {**each.error.details, "reason": "raised", "rules": 1},
                        }
                    )
                )
            else:
                found.append(public(answer.plugin, each))
    return found


def _not_checked(answer: Errored | Skipped, report: Report, *, rules: int | None) -> None:
    details = {} if rules is None else {"rules": rules}
    if isinstance(answer, Errored):
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
    elif answer.reason == Reason.not_configured:
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
    lead: str = "rules did not run",
) -> Failure:
    return Failure(
        category=category,
        system=plugin,
        item=plugin,
        message=f"{plugin}'s {lead}: {reason}: {detail}",
        hint=_RULES_HINT,
        details={"reason": reason, **(details or {})},
    )


def _invalid_plugin(subject: Subject, error: ValidationError) -> Failure:
    name = str(subject.record.get("name") or PLUGIN_KIND)
    return Failure(
        category=ErrorCategory.INVALID,
        system=KEY,
        item=name,
        message=f"{name} was not checked: its {PLUGIN_KIND} record is invalid: "
        f"{plural(error.error_count(), 'field error')}",
        hint="pipe `untaped plugin list --format pipe` output",
        details={"reason": "invalid-record"},
    )
