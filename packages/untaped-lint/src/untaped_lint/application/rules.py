"""The ``Linter`` contract and the one way to write a rule: ``@rule`` in a ``Rules`` host.

A provider fills ``Linter`` (``PluginSpec.provides={"lint": ...}``). The stock
filling is :class:`Rules`: list the rule functions in ``RULES`` (or write
``@rule`` methods on the subclass), and it validates each subject once as the
model its rules read, runs each rule under its own ``try`` and carries a
raise as a finding row with ``error`` set, which the engine counts as a
failure, never as a finding.
"""

from __future__ import annotations

import inspect
from abc import abstractmethod
from collections.abc import Callable, Iterable, Iterator
from typing import ClassVar, cast

from pydantic import ValidationError

from untaped.contracts import Contract, listing
from untaped.sdk import Failure, Record, experimental, kind_of
from untaped_lint.application.ports import RuleFunction
from untaped_lint.records import Finding, Location, Rule, Severity, Subject

#: Marks a provider base's own helper, so doctor's unused-method check skips it
#: (``untaped.contracts`` reads the same attribute).
_PROVIDER_HELPER = "__untaped_provider_helper__"


@experimental
class Linter(Contract):
    """Rules over subjects: what ``untaped lint`` asks every provider."""

    @abstractmethod
    def rules(self) -> list[Rule]:
        """Every rule the provider enforces, ids bare: the engine prefixes the plugin."""

    @listing
    @abstractmethod
    def findings(self, subject: Subject) -> list[Finding]:
        """The findings of the rules that read ``subject.kind``, rule ids bare."""


def rule(
    id: str,
    *,
    reads: type[Record],
    severity: Severity,
    summary: str,
    renamed_from: tuple[str, ...] = (),
) -> Callable[[Callable[..., Iterable[Finding]]], RuleFunction]:
    """Declare a rule: the function (or ``Rules`` method) takes the ``reads`` model.

    The function is the rule's constant: ``finding(my_rule, ...)`` reads its
    id and severity from it, and ``my_rule.rule`` is its :class:`Rule`.
    """
    kind = kind_of(reads)
    if kind is None:
        raise TypeError(f"rule {id!r} reads {reads.__name__}, which declares no kind")
    declared = Rule(
        id=id, reads=kind, severity=severity, summary=summary, renamed_from=renamed_from
    )

    def mark(function: Callable[..., Iterable[Finding]]) -> RuleFunction:
        marked = cast("RuleFunction", function)
        marked.rule = declared
        marked.model = reads
        setattr(function, _PROVIDER_HELPER, True)
        return marked

    return mark


def finding(
    rule: Rule | RuleFunction,
    target: str,
    message: str,
    *,
    location: Location | None = None,
    hint: str | None = None,
    fix: list[str] | None = None,
) -> Finding:
    """A finding of ``rule`` about ``target``, with the rule's id and severity."""
    declared = rule if isinstance(rule, Rule) else rule.rule
    return Finding(
        rule=declared.id,
        severity=declared.severity,
        target=target,
        message=message,
        location=location,
        hint=hint,
        fix=fix,
    )


class Rules(Linter):
    """The stock ``Linter``: hosts ``RULES`` plus its own ``@rule`` methods.

    ``class AcmeRules(Rules): RULES = (no_print, ...)``. A rule that raises
    becomes one finding row with ``error`` set; the other rules' findings
    still count.
    """

    RULES: ClassVar[tuple[RuleFunction, ...]] = ()

    def rules(self) -> list[Rule]:
        return [function.rule for function in self._functions()]

    def findings(self, subject: Subject) -> list[Finding]:
        found: list[Finding] = []
        models: dict[type[Record], Record | ValidationError] = {}
        for function in self._functions():
            if function.rule.reads != subject.kind:
                continue
            if function.model not in models:  # validated once per model read
                try:
                    models[function.model] = function.model.model_validate(subject.record)
                except ValidationError as exc:
                    models[function.model] = exc
            model = models[function.model]
            if isinstance(model, ValidationError):
                found.append(_raised(function.rule, subject, model))
                continue
            try:
                found.extend(function(model))
            except Exception as exc:  # one rule's raise is its row; the others still run
                found.append(_raised(function.rule, subject, exc))
        return found

    def _functions(self) -> Iterator[RuleFunction]:
        """``RULES``, then the ``@rule`` methods (a subclass's override wins), bound."""
        listed: tuple[object, ...] = type(self).RULES
        yield from type(self).RULES
        seen: set[str] = set()
        for klass in type(self).__mro__:
            for name, value in vars(klass).items():
                if name in seen:
                    continue
                seen.add(name)
                # Only a function defined here is a method; an alias of a listed
                # rule, or of a module, is not bound a second time.
                if (
                    inspect.isfunction(value)
                    and value not in listed
                    and isinstance(getattr(value, "rule", None), Rule)
                ):
                    yield cast("RuleFunction", getattr(self, name))


def _raised(declared: Rule, subject: Subject, error: Exception) -> Finding:
    target = str(subject.record.get("name") or subject.kind)
    return Finding(
        rule=declared.id,
        severity="error",
        target=target,
        message=str(error) or type(error).__name__,
        error=Failure.from_exception(error),
    )
