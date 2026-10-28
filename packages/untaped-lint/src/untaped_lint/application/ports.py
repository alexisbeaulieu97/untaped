"""The shapes the rule machinery relies on: a rule function, as ``@rule`` leaves it."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from untaped.sdk import Record
from untaped_lint.records import Finding, Severity


class RuleFunction(Protocol):
    """A function ``@rule`` decorated: called with a record of its model, it yields findings."""

    id: str
    """Bare, the function's name in kebab case."""
    severity: Severity
    summary: str
    """The docstring's first paragraph, as one line."""
    model: type[Record]
    """The record model the rule reads, from its first parameter."""

    def __call__(self, *args: Any) -> Iterable[Finding]: ...
