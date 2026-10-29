"""Which findings a run reports: ids resolved, suppressions and selections applied.

A suppression is ``<rule>[:<target-glob>]``, one spelling wherever it is
written (the profile's ``lint.ignore``, ``--ignore``); ``--select`` narrows
to rules, PATHS to ``location.path`` globs. Selection narrows what is
reported, never what is read. A public id is ``<key>.<id>``; a bare id is a
shorthand honoured when exactly one loaded rule carries it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from untaped.sdk import UsageError
from untaped_lint.domain.glob import matches
from untaped_lint.records import Finding, Rule, location_text

_SPELLING = re.compile(r"^(?:[a-z0-9][a-z0-9-]*\.)?[A-Za-z0-9][A-Za-z0-9-]*$")


@dataclass(frozen=True)
class Suppression:
    """``rule`` everywhere, or only where the target matches ``target``."""

    rule: str
    target: str | None = None


def parse_suppression(text: str, *, option: str) -> Suppression:
    """``<rule>[:<glob>]``; a malformed rule spelling is a usage error naming ``option``."""
    rule, colon, target = text.partition(":")
    if not _SPELLING.match(rule) or (colon and not target):
        raise UsageError(f"{option} {text!r} is not <rule> or <rule>:<target-glob>")
    return Suppression(rule, target or None)


def parse_rule(text: str, *, option: str) -> str:
    """One rule id as typed: ``<key>.<id>`` or a bare ``<id>``."""
    if not _SPELLING.match(text):
        raise UsageError(f"{option} {text!r} is not a rule id such as lint.error-case")
    return text


@dataclass
class Resolver:
    """Turns typed ids into public ones against the loaded rules, warning once per id."""

    rules: Mapping[str, Rule]
    """The loaded rules by public id."""
    warnings: list[str] = field(default_factory=list)
    _said: set[str] = field(default_factory=set)

    def resolve(self, typed: str) -> set[str]:
        """The public ids ``typed`` names: itself, or a bare id's one owner."""
        if typed in self.rules:
            return {typed}
        if "." not in typed:
            owners = sorted(public for public in self.rules if public.split(".", 1)[1] == typed)
            if len(owners) == 1:
                return set(owners)
            if owners:
                self._warn(typed, f"`{typed}` names {', '.join(owners)}; write the one you mean")
                return set()
        self._warn(typed, f"`{typed}`: no installed rule; is its plugin installed?")
        return set()

    def _warn(self, typed: str, text: str) -> None:
        if typed not in self._said:
            self._said.add(typed)
            self.warnings.append(text)


@dataclass(frozen=True)
class Selection:
    """What a run reports, its ids already public."""

    select: frozenset[str] | None = None
    """Only these rules; ``None`` is every rule."""
    ignore: tuple[tuple[frozenset[str], str | None], ...] = ()
    paths: tuple[str, ...] = ()
    only: tuple[str, ...] = ()
    """Target globs: report a finding only when its target matches one."""
    exact: frozenset[str] = frozenset()
    """Literal targets, compared exactly, never as globs."""
    locations: frozenset[str] = frozenset()
    """Literal locations as a table reads them (``job_templates/14``, ``a.yml#2``)."""
    unchecked: frozenset[str] = frozenset()
    """Rules saying nothing else ran on a target: ``select`` and PATHS never drop them."""

    @classmethod
    def build(
        cls,
        resolver: Resolver,
        *,
        select: Iterable[str] = (),
        ignore: Iterable[Suppression] = (),
        paths: Iterable[str] = (),
        only: Iterable[str] = (),
        exact: Iterable[str] = (),
        locations: Iterable[str] = (),
        unchecked: Iterable[str] = (),
    ) -> Selection:
        chosen = [resolver.resolve(each) for each in select]
        if chosen and not any(chosen):
            # Reporting nothing would pass a CI step that pins a misspelled rule.
            raise UsageError(f"--select {'; '.join(resolver.warnings)}")
        return cls(
            select=frozenset().union(*chosen) if chosen else None,
            ignore=tuple((frozenset(resolver.resolve(each.rule)), each.target) for each in ignore),
            paths=tuple(paths),
            only=tuple(only),
            exact=frozenset(exact),
            locations=frozenset(locations),
            unchecked=frozenset(unchecked),
        )

    def rule_selected(self, rule: str) -> bool:
        return self.select is None or rule in self.select

    def keeps(self, finding: Finding) -> bool:
        """Whether ``finding`` (its rule id public) is reported."""
        narrowed = finding.rule not in self.unchecked
        if narrowed and not self.rule_selected(finding.rule):
            return False
        for rules, target in self.ignore:
            if finding.rule in rules and (target is None or matches(target, finding.target)):
                return False
        if self.paths and narrowed:
            path = getattr(finding.location, "path", None)
            if path is None or not any(matches(pattern, path) for pattern in self.paths):
                return False
        if self.locations and narrowed and location_text(finding.location) not in self.locations:
            return False
        if self.only and not any(matches(pattern, finding.target) for pattern in self.only):
            return False
        return not self.exact or finding.target in self.exact
