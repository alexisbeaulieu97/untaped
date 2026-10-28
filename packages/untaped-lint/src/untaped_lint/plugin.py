"""The ``lint`` plugin class: what the plugin has, each under its own name.

The ``untaped.plugins`` entry point names :class:`LintPlugin`, in this leaf
module: the class imports every command.
"""

from __future__ import annotations

from untaped.sdk import DoctorCheck, Plugin, StubSkill, experimental
from untaped_lint.application.rules import Linter, TreeReader
from untaped_lint.commands.lint import Lint, New, Rules
from untaped_lint.doctor import ignore_ids_check
from untaped_lint.settings import LintSettings


class LintPlugin(Plugin):
    """Check plugins, repositories and records against rules, with ids to ignore."""

    settings = LintSettings
    stability = experimental
    runs = Lint
    commands = (Rules, New)
    offers = (Linter, TreeReader)
    ignore_ids = DoctorCheck(title="lint.ignore ids", run=ignore_ids_check)
    skill = StubSkill(
        "untaped lint: a repository's conventions as rules (its own, named in "
        "`.untaped/lint.yml`, each plugin's, and the checks every untaped plugin "
        "passes) and the findings they report, each with a rule id. Use when the work "
        "is in a repository with `.untaped/lint.yml` or untaped plugins, when "
        "`untaped lint` fails, or when the user explains a convention the code should "
        "keep."
    )


__all__ = ["LintPlugin"]
