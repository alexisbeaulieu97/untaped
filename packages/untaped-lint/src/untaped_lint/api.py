"""What another plugin may import from lint: the records, the ``Linter`` contract and ``@rule``.

A plugin with rules fills :class:`Linter`, normally through :class:`Rules`::

    @rule("no-print", reads=Plugin, severity="error", summary="No print() calls.")
    def no_print(plugin: Plugin) -> Iterator[Finding]: ...

    class AcmeRules(Rules):
        RULES = (no_print,)

and offers ``AcmeRules()`` in ``PluginSpec.provides={"lint": ...}``. A
command of its own runs rules over its records with :func:`lint`, which
prints and exits by lint's rules.
"""

from __future__ import annotations

from untaped_lint.application.output import lint
from untaped_lint.application.ports import RuleFunction
from untaped_lint.application.rules import Linter, Rules, finding, rule
from untaped_lint.cli.options import IgnoreOption, SelectOption, StrictOption
from untaped_lint.config import LintConfig
from untaped_lint.records import (
    DocumentLocation,
    FileLocation,
    Finding,
    Location,
    ResourceLocation,
    Rule,
    Severity,
    Subject,
)

__all__ = [
    "DocumentLocation",
    "FileLocation",
    "Finding",
    "IgnoreOption",
    "LintConfig",
    "Linter",
    "Location",
    "ResourceLocation",
    "Rule",
    "RuleFunction",
    "Rules",
    "SelectOption",
    "Severity",
    "StrictOption",
    "Subject",
    "finding",
    "lint",
    "rule",
]
