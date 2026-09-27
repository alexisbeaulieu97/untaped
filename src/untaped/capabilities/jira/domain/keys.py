"""Issue key validation: what may be interpolated into an issue's REST path."""

from __future__ import annotations

import re

from untaped.capability_api import UsageError, q

# A Jira project key starts with a letter and holds letters, digits and
# underscores; an issue key appends ``-<number>``. Numeric issue ids pass too.
_ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9_]*-[0-9]+|[0-9]+")


def validate_issue_key(value: str) -> str:
    """``value`` as an uppercase issue key (or numeric id); a usage error otherwise."""
    key = value.strip().upper()
    if not _ISSUE_KEY.fullmatch(key):
        raise UsageError(f"invalid issue key {q(value)}; expected PROJECT-123 or a numeric id")
    return key
