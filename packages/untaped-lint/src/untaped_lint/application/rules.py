"""The ``Linter`` contract and the one way to write a rule: ``@rule`` in a ``Rules`` host.

A provider fills ``Linter`` (its plugin class lists it in ``fills``). The stock
filling is :class:`Rules`: list the rule functions in ``RULES`` (or write
``@rule`` methods on the subclass), and it hands its own copy of each
record to the rules that read its model, runs each rule under its own
``try`` and carries a raise as a finding row with ``error`` set, which the
engine counts as a failure, never as a finding.
"""

from __future__ import annotations

import inspect
from abc import abstractmethod
from annotationlib import Format, ForwardRef
from collections.abc import Callable, Iterable, Iterator
from types import ModuleType
from typing import ClassVar, cast, evaluate_forward_ref

from pydantic import ValidationError

from untaped.sdk import Failure, Record, experimental
from untaped.sdk.contracts import Contract, Provider
from untaped_lint.application.ports import RuleFunction
from untaped_lint.records import Finding, Location, Rule, Severity, Tree, declared_kind

#: Marks a provider base's own helper, so doctor's unused-method check skips it
#: (``untaped.sdk.contracts`` reads the same attribute).
_PROVIDER_HELPER = "__untaped_provider_helper__"


@experimental
class Linter(Contract):
    """Rule sets ``untaped lint`` runs.

    Rules over records: what ``untaped lint`` asks every provider.
    """

    @abstractmethod
    def rules(self) -> list[Rule]:
        """Every rule the provider enforces, ids bare: the engine prefixes the plugin."""

    @abstractmethod
    def findings(self, record: Record) -> list[Finding]:
        """The findings of the rules that read ``record``'s model, rule ids bare."""


@experimental
class TreeReader(Contract):
    """File kinds ``untaped lint`` can read from a repository.

    Files of a kind read into records: what a ``readers:`` entry of the repo
    file runs.
    """

    @abstractmethod
    def read(self, tree: Tree) -> list[Record]:
        """The records the files of ``tree`` hold (already narrowed to the entry's globs).

        A file the reader can't read is carried in a record for the owner's
        own rule to report, never raised.
        """


def rule(*, severity: Severity) -> Callable[[Callable[..., Iterable[Finding]]], RuleFunction]:
    """Declare a rule: everything but its severity is read from the function.

    Its id is the function's name in kebab case (``no_print`` is
    ``no-print``), its summary the docstring's first paragraph, and what it
    reads the model its first parameter (after ``self``) is annotated with.
    The function is the rule's constant: ``finding(my_rule, ...)`` reads its
    id and severity from it, and ``my_rule.rule`` is its :class:`Rule`.
    """

    def mark(function: Callable[..., Iterable[Finding]]) -> RuleFunction:
        reads = _reads(function)
        try:
            declared = Rule(
                id=function.__name__.replace("_", "-"),
                reads=declared_kind(reads),
                severity=severity,
                summary=_summary(function),
            )
        except ValidationError:
            raise TypeError(
                f"rule {function.__qualname__}: its name is its id, so it is lower-case "
                "words joined by `_` (no_print is no-print)"
            ) from None
        marked = cast("RuleFunction", function)
        marked.rule = declared
        marked.model = reads
        setattr(function, _PROVIDER_HELPER, True)
        return marked

    return mark


def _reads(function: Callable[..., object]) -> type[Record]:
    """The record model ``function``'s first parameter (after ``self``) is annotated with.

    Only that annotation is evaluated: the others may name what the module
    imports for type checking alone.
    """
    where = f"rule {function.__qualname__}"
    signature = inspect.signature(function, annotation_format=Format.FORWARDREF)
    parameters = list(signature.parameters.values())
    if parameters[:1] and parameters[0].name == "self":
        parameters = parameters[1:]
    if not parameters:
        raise TypeError(f"{where} takes no record: its first parameter is what it reads")
    hint = parameters[0].annotation
    if isinstance(hint, str):  # the module defers its annotations
        hint = ForwardRef(hint, module=function.__module__)
    if isinstance(hint, ForwardRef):
        try:
            hint = evaluate_forward_ref(hint, globals=getattr(function, "__globals__", None))
        except NameError as exc:
            raise TypeError(f"{where}: its record model doesn't resolve: {exc}") from None
    if not (isinstance(hint, type) and issubclass(hint, Record)):
        raise TypeError(f"{where}: annotate {parameters[0].name!r} with the record model it reads")
    return hint


def _summary(function: Callable[..., object]) -> str:
    """The first paragraph of ``function``'s docstring, as one line."""
    doc = inspect.getdoc(function)
    if not doc:
        raise TypeError(
            f"rule {function.__qualname__} has no docstring: its first paragraph is the summary"
        )
    return " ".join(doc.split("\n\n", 1)[0].split())


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


class Rules(Linter, Provider):
    """The stock ``Linter``: hosts ``RULES`` plus its own ``@rule`` methods.

    ``class AcmeRules(Rules): RULES = (no_print, ...)``. A rule that raises
    becomes one finding row with ``error`` set; the other rules' findings
    still count.
    """

    RULES: ClassVar[tuple[RuleFunction, ...]] = ()

    @classmethod
    def from_module(cls, *modules: ModuleType) -> type[Rules]:
        """A host class for the ``@rule`` functions ``modules`` define (not ones they import)."""
        functions = tuple(
            cast("RuleFunction", value)
            for module in modules
            for value in vars(module).values()
            if inspect.isfunction(value)
            and value.__module__ == module.__name__
            and isinstance(getattr(value, "rule", None), Rule)
        )
        host = type("ModuleRules", (cls,), {"RULES": functions, "__module__": cls.__module__})
        return cast("type[Rules]", host)

    def rules(self) -> list[Rule]:
        return [function.rule for function in self._functions()]

    def findings(self, record: Record) -> list[Finding]:
        found: list[Finding] = []
        record = record.model_copy(deep=True)  # what a rule changes, no other provider sees
        for function in self._functions():
            if type(record) is not function.model:
                continue
            try:
                found.extend(function(record))
            except Exception as exc:  # one rule's raise is its row; the others still run
                found.append(_raised(function.rule, record, exc))
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
                    listed = (*listed, value)  # a second name for it binds nothing new
                    yield cast("RuleFunction", getattr(self, name))


def _raised(declared: Rule, record: Record, error: Exception) -> Finding:
    target = str(getattr(record, "name", None) or declared.reads)
    return Finding(
        rule=declared.id,
        severity="error",
        target=target,
        message=str(error) or type(error).__name__,
        error=Failure.from_exception(error),
    )
