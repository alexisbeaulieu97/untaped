"""The shapes the rule machinery relies on: a rule function, as ``@rule`` leaves it."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from untaped.sdk import Record
from untaped_lint.records import Finding, Rule


class RuleFunction(Protocol):
    """A function ``@rule`` decorated: called with the subject's model, it yields findings."""

    rule: Rule
    model: type[Record]
    """The model the rule reads its subject as."""

    def __call__(self, *args: Any) -> Iterable[Finding]: ...
