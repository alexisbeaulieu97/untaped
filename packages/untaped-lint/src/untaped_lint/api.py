"""What another plugin may import from lint: the records, the ``Linter`` contract and ``@rule``.

A plugin with rules fills :class:`Linter`, normally through :class:`Rules`
(a repository's ``.untaped/lint/*.py`` modules write the same ``@rule``
functions and import only this module)::

    @rule(severity="error")
    def no_print(plugin: InstalledPlugin) -> Iterator[Finding]:
        \"\"\"No print() calls.\"\"\"

    class AcmeRules(Rules):
        RULES = (no_print,)

and lists ``AcmeRules`` in its plugin class's ``fills``. A plugin that turns
a repository's files into its own records fills :class:`TreeReader`. A
command of its own runs rules over its records from the :class:`LintConfig`
:func:`lint_config` builds (the profile's ``lint.ignore`` and the
repository's lint file), so the plugin never reads lint's settings, with the
:class:`Findings` bundle a lint command takes and :func:`lint_report`, which
runs the engine.
"""

from __future__ import annotations

from untaped.sdk import InstalledPlugin
from untaped_lint.application.output import lint_report
from untaped_lint.application.ports import RuleFunction
from untaped_lint.application.rules import Linter, Rules, TreeReader, finding, rule, rule_row
from untaped_lint.commands._lint import Findings
from untaped_lint.config import LintConfig, lint_config
from untaped_lint.domain.report import Report
from untaped_lint.records import (
    DocumentLocation,
    File,
    FileLocation,
    Finding,
    Location,
    ResourceLocation,
    Rule,
    Severity,
    Tree,
    target_of,
)

__all__ = [
    "DocumentLocation",
    "File",
    "FileLocation",
    "Finding",
    "Findings",
    "InstalledPlugin",
    "LintConfig",
    "Linter",
    "Location",
    "Report",
    "ResourceLocation",
    "Rule",
    "RuleFunction",
    "Rules",
    "Severity",
    "Tree",
    "TreeReader",
    "finding",
    "lint_config",
    "lint_report",
    "rule",
    "rule_row",
    "target_of",
]
