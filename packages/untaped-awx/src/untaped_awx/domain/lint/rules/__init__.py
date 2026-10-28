"""The awx rules, one per module, each a pure function of :class:`Resources`.

``RULES`` lists them in the order the ``linting`` guide's rule table does; ``AwxRules``
hosts it, and ``untaped_awx.api`` exports each function, the constant
``finding()`` takes.
"""

from __future__ import annotations

from untaped_awx.domain.lint.rules.branch_override_ignored import branch_override_ignored
from untaped_awx.domain.lint.rules.empty_inventory import empty_inventory
from untaped_awx.domain.lint.rules.ignored_launch_field import ignored_launch_field
from untaped_awx.domain.lint.rules.invalid_document import invalid_document
from untaped_awx.domain.lint.rules.missing_reference import missing_reference
from untaped_awx.domain.lint.rules.plaintext_secret import plaintext_secret
from untaped_awx.domain.lint.rules.playbook_missing import playbook_missing
from untaped_awx.domain.lint.rules.survey_default_missing import survey_default_missing
from untaped_awx.domain.lint.rules.unknown_field import unknown_field
from untaped_awx.domain.lint.rules.unresolved_reference import unresolved_reference
from untaped_lint.api import RuleFunction

RULES: tuple[RuleFunction, ...] = (
    missing_reference,
    unresolved_reference,
    ignored_launch_field,
    branch_override_ignored,
    survey_default_missing,
    unknown_field,
    playbook_missing,
    empty_inventory,
    plaintext_secret,
    invalid_document,
)

__all__ = [
    "RULES",
    "branch_override_ignored",
    "empty_inventory",
    "ignored_launch_field",
    "invalid_document",
    "missing_reference",
    "plaintext_secret",
    "playbook_missing",
    "survey_default_missing",
    "unknown_field",
    "unresolved_reference",
]
