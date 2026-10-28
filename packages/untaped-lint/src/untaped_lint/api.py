"""What another plugin may import from lint: the records, the ``Linter`` contract and ``@rule``.

A plugin with rules fills :class:`Linter`, normally through :class:`Rules`
(a repository's ``.untaped/lint/*.py`` modules write the same ``@rule``
functions and import only this module)::

    @rule("no-print", reads=InstalledPlugin, severity="error", summary="No print() calls.")
    def no_print(plugin: InstalledPlugin) -> Iterator[Finding]: ...

    class AcmeRules(Rules):
        RULES = (no_print,)

and lists ``AcmeRules`` in its plugin class's ``fills``. A plugin that turns
a repository's files into its own subjects fills :class:`TreeReader`. A
command of its own runs rules over its records from the :class:`LintConfig`
:func:`lint_config` builds (the profile's ``lint.ignore`` and the
repository's lint file), so the plugin never reads lint's settings, with the
:class:`Findings` bundle a lint command takes and :func:`lint_report`, which
runs the engine.
"""

from __future__ import annotations

from untaped.sdk import InstalledPlugin
from untaped_lint.application.lint_config import lint_config
from untaped_lint.application.output import lint_report
from untaped_lint.application.ports import RuleFunction
from untaped_lint.application.rules import Linter, Rules, TreeReader, finding, rule
from untaped_lint.commands._lint import Findings
from untaped_lint.config import LintConfig
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
    Subject,
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
    "Subject",
    "Tree",
    "TreeReader",
    "finding",
    "lint_config",
    "lint_report",
    "rule",
    "target_of",
]
