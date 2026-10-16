"""What another plugin may import from lint: the records, the ``Linter`` contract and ``@rule``.

A plugin with rules fills :class:`Linter`, normally through :class:`Rules`
(a repository's ``.untaped/lint/*.py`` modules write the same ``@rule``
functions and import only this module)::

    @rule("no-print", reads=Plugin, severity="error", summary="No print() calls.")
    def no_print(plugin: Plugin) -> Iterator[Finding]: ...

    class AcmeRules(Rules):
        RULES = (no_print,)

and offers ``AcmeRules()`` in ``PluginSpec.provides={"lint": ...}``. A
plugin that turns a repository's files into its own subjects fills
:class:`TreeReader`. A command of its own runs rules over its records with
:func:`lint`, which prints and exits by lint's rules.
"""

from __future__ import annotations

from untaped.sdk import Plugin
from untaped_lint.application.output import lint
from untaped_lint.application.ports import RuleFunction
from untaped_lint.application.rules import Linter, Rules, TreeReader, finding, rule
from untaped_lint.cli.options import IgnoreOption, SelectOption, StrictOption
from untaped_lint.config import LintConfig
from untaped_lint.records import (
    DocumentLocation,
    File,
    FileLocation,
    Finding,
    Location,
    ResourceLocation,
    Rule,
    Severity,
    Subject,
    Tree,
    target_of,
)

__all__ = [
    "DocumentLocation",
    "File",
    "FileLocation",
    "Finding",
    "IgnoreOption",
    "LintConfig",
    "Linter",
    "Location",
    "Plugin",
    "ResourceLocation",
    "Rule",
    "RuleFunction",
    "Rules",
    "SelectOption",
    "Severity",
    "StrictOption",
    "Subject",
    "Tree",
    "TreeReader",
    "finding",
    "lint",
    "rule",
    "target_of",
]
