"""JQL value objects for Jira issue search."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict


class JiraIssueSearchFilters(BaseModel):
    """Common issue search shortcuts plus an optional raw JQL base.

    ``scope_jql`` always applies and is ANDed with ``raw_jql`` and the
    shortcuts (``raw_jql``'s ``ORDER BY`` wins over the scope's).
    ``default_jql`` is the whole query only when nothing else filters.
    """

    model_config = ConfigDict(frozen=True)

    scope_jql: str | None = None
    default_jql: str | None = None
    raw_jql: str | None = None
    project: str | None = None
    assignee: str | None = None
    status: str | None = None
    text: str | None = None
    sprint: str | None = None

    def to_jql(self) -> str:
        scope, scope_order_by = _split_order_by(_stripped(self.scope_jql))
        base, order_by = _split_order_by(_stripped(self.raw_jql))
        parts = [f"({clause})" for clause in (scope, base) if clause]
        parts.extend(self._shortcut_clauses())
        default_order_by: str | None = None
        if not parts:
            default, default_order_by = _split_order_by(_stripped(self.default_jql))
            parts = [default] if default else []
        jql = " AND ".join(parts)
        order = order_by or scope_order_by or default_order_by or "ORDER BY updated DESC"
        return f"{jql} {order}".strip()

    def _shortcut_clauses(self) -> list[str]:
        clauses: list[str] = []
        if self.project:
            clauses.append(f"project = {_quote_project(self.project)}")
        if self.assignee:
            clauses.append(f"assignee = {_quote_or_current_user(self.assignee)}")
        if self.status:
            clauses.append(f"status = {_quote(self.status)}")
        if self.text:
            clauses.append(f"text ~ {_quote(self.text)}")
        if self.sprint:
            function = _sprint_function(self.sprint)
            if function is not None:
                clauses.append(f"sprint in {function}")
            else:
                clauses.append(f"sprint = {_quote_sprint(self.sprint)}")
        return clauses


def _stripped(jql: str | None) -> str | None:
    return jql.strip() if jql else None


def _split_order_by(jql: str | None) -> tuple[str | None, str | None]:
    if not jql:
        return None, None
    order_start = _find_order_by(jql)
    if order_start is None:
        return jql, None
    return jql[:order_start].strip(), jql[order_start:].strip()


def _find_order_by(jql: str) -> int | None:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(jql):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if index == 0 and re.match(r"order\s+by\s+", jql, flags=re.IGNORECASE):
            return index
        if char.isspace() and re.match(r"\s+order\s+by\s+", jql[index:], flags=re.IGNORECASE):
            return index
    return None


def _quote_project(value: str) -> str:
    return value if re.fullmatch(r"[A-Z][A-Z0-9_]*", value) else _quote(value)


def _quote_or_current_user(value: str) -> str:
    return "currentUser()" if value in {"@me", "me", "currentUser()"} else _quote(value)


_SPRINT_FUNCTIONS = {
    name.casefold(): name for name in ("openSprints()", "futureSprints()", "closedSprints()")
}


def _sprint_function(value: str) -> str | None:
    """Return the canonical JQL sprint function named by ``value``, if any."""
    return _SPRINT_FUNCTIONS.get(value.strip().casefold())


def _quote_sprint(value: str) -> str:
    return value if value.isdecimal() else _quote(value)


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
