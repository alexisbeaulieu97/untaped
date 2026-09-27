"""Issue and project key validation: what may be interpolated into a REST path."""

from __future__ import annotations

import re

from untaped.capability_api import UsageError, q

# A Jira project key starts with a letter and holds letters, digits and
# underscores; an issue key appends ``-<number>``. Numeric ids pass too.
PROJECT_KEY_PATTERN = r"[A-Z][A-Z0-9_]*"
_PROJECT_KEY = re.compile(rf"{PROJECT_KEY_PATTERN}|[0-9]+")
_ISSUE_KEY = re.compile(rf"{PROJECT_KEY_PATTERN}-[0-9]+|[0-9]+")


def validate_issue_key(value: str) -> str:
    """``value`` as an uppercase issue key (or numeric id); a usage error otherwise."""
    return _validate(value, _ISSUE_KEY, "issue key", "PROJECT-123")


def validate_project_key(value: str) -> str:
    """``value`` as an uppercase project key (or numeric id); a usage error otherwise."""
    return _validate(value, _PROJECT_KEY, "project key", "PROJECT")


def _validate(value: str, pattern: re.Pattern[str], what: str, example: str) -> str:
    key = value.strip().upper()
    if not pattern.fullmatch(key):
        raise UsageError(f"invalid {what} {q(value)}; expected {example} or a numeric id")
    return key
