"""Jira REST payloads turned into the records ``untaped jira`` writes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from untaped_jira.records import (
    Board,
    Comment,
    Issue,
    IssueLink,
    Project,
    Sprint,
    Transition,
    User,
)

# Jira ``fields`` requested for list rows (search) and the richer single-issue
# detail view (get); the client requests exactly what the models flatten.
ISSUE_ROW_FIELDS: tuple[str, ...] = (
    "summary",
    "status",
    "assignee",
    "updated",
    "issuetype",
    "priority",
)
ISSUE_DETAIL_FIELDS: tuple[str, ...] = (
    *ISSUE_ROW_FIELDS,
    "reporter",
    "labels",
    "created",
    "resolution",
    "description",
    "issuelinks",
)


def parse_user(data: Mapping[str, Any]) -> User:
    """The user row of a raw ``/myself`` payload."""
    return User.model_validate(
        _present(
            data,
            name="name",
            key="key",
            display_name="displayName",
            email_address="emailAddress",
        )
    )


def parse_issue(data: Mapping[str, Any], *, detail: bool = False) -> Issue:
    """An issue row from a raw Jira issue, flattening its nested ``fields``.

    ``detail`` also fills the fields ``issues get`` shows (``ISSUE_DETAIL_FIELDS``).
    """
    fields = data.get("fields") or {}
    row: dict[str, Any] = {
        "key": data.get("key"),
        "summary": _text(fields.get("summary")),
        "status": _name(fields.get("status")),
        "assignee": _display_name(fields.get("assignee")),
        "updated_at": _timestamp(fields.get("updated")),
        "url": _browser_url(data),
        "api_url": _api_url(data),
        "issue_type": _name(fields.get("issuetype")),
        "priority": _name(fields.get("priority")),
    }
    if detail:
        labels = fields.get("labels") or []
        row |= {
            "reporter": _display_name(fields.get("reporter")),
            "labels": [str(label) for label in labels] if isinstance(labels, list) else [],
            "created_at": _timestamp(fields.get("created")),
            "resolution": _name(fields.get("resolution")),
            "description": _text(fields.get("description")),
            "links": _links(fields.get("issuelinks")),
        }
    return Issue.model_validate(row)


def parse_comment(data: Mapping[str, Any], *, issue_key: str) -> Comment:
    """A comment row from a raw Jira comment (nested ``author``, ``created``, ``self``)."""
    return Comment(
        id=str(data.get("id") or ""),
        issue_key=issue_key,
        author=_display_name(data.get("author")),
        created_at=_timestamp(data.get("created")),
        updated_at=_timestamp(data.get("updated")),
        body=_text(data.get("body")),
        api_url=_api_url(data),
    )


def _link(link: Any) -> IssueLink | None:
    """A link row from one raw ``issuelinks`` entry; ``None`` if malformed."""
    if not isinstance(link, dict):
        return None
    raw_type = link.get("type")
    link_type: dict[str, Any] = raw_type if isinstance(raw_type, dict) else {}
    for direction in ("outward", "inward"):
        other = link.get(f"{direction}Issue")
        if isinstance(other, dict) and isinstance(other.get("key"), str):
            fields = other.get("fields") or {}
            return IssueLink(
                key=other["key"],
                summary=_text(fields.get("summary")),
                status=_name(fields.get("status")),
                type=str(link_type.get("name") or ""),
                direction=direction,
                relation=str(link_type.get(direction) or ""),
                url=_browser_url(other),
            )
    return None


def parse_transition(data: Mapping[str, Any]) -> Transition:
    """A transition row from a raw Jira transition; ``to_status`` is its target's name."""
    row = _present(data, id="id", name="name")
    if "to" in data:
        row["to_status"] = _name(data.get("to")) or None
    return Transition.model_validate(row)


def parse_project(data: Mapping[str, Any]) -> Project:
    """A project row from a raw Jira project."""
    return Project.model_validate(
        _present(data, key="key", name="name", id="id", project_type_key="projectTypeKey")
    )


def parse_board(data: Mapping[str, Any]) -> Board:
    """A board row from a raw Jira Software board."""
    return Board.model_validate(_present(data, id="id", name="name", type="type", api_url="self"))


def parse_sprint(data: Mapping[str, Any]) -> Sprint:
    """A sprint row from a raw Jira Software sprint; odd timestamps become ``None``."""
    row = _present(data, id="id", name="name", state="state", goal="goal")
    row |= _present(data, origin_board_id="originBoardId")
    row["start_at"] = _timestamp(data.get("startDate"))
    row["end_at"] = _timestamp(data.get("endDate"))
    return Sprint.model_validate(row)


def _present(data: Mapping[str, Any], **names: str) -> dict[str, Any]:
    """Each field whose Jira key (``field=jiraKey``) ``data`` has, under the field's name."""
    return {field: data[key] for field, key in names.items() if key in data}


def _links(value: Any) -> list[IssueLink]:
    if not isinstance(value, list):
        return []
    return [link for raw in value if (link := _link(raw)) is not None]


_ADF_INLINE_TYPES = frozenset(
    {"text", "hardBreak", "mention", "emoji", "inlineCard", "date", "status"}
)


def _text(value: Any) -> str:
    """Coerce a Jira field value to display text.

    API v3 returns rich-text fields as Atlassian Document Format (ADF)
    objects; those flatten to plain text. Any other non-string value is
    JSON-dumped rather than failing validation.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get("type") == "doc":
        blocks: list[str] = []
        _adf_blocks(value, blocks)
        return "\n".join(block for block in blocks if block)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _adf_blocks(node: dict[str, Any], blocks: list[str]) -> None:
    """Append one line of plain text per ADF block under ``node`` to ``blocks``.

    Nodes holding only inline children (paragraphs, headings, ...) become one
    block; containers (lists, tables, panels, ...) recurse.
    """
    inline: list[str] = []
    for child in _adf_children(node):
        if child.get("type") in _ADF_INLINE_TYPES:
            inline.append(_adf_inline(child))
            continue
        if inline:
            blocks.append("".join(inline))
            inline = []
        if any(grand.get("type") not in _ADF_INLINE_TYPES for grand in _adf_children(child)):
            _adf_blocks(child, blocks)
        else:
            blocks.append(_adf_inline(child))
    if inline:
        blocks.append("".join(inline))


def _adf_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    content = node.get("content")
    if not isinstance(content, list):
        return []
    return [child for child in content if isinstance(child, dict)]


def _adf_inline(node: dict[str, Any]) -> str:
    """Plain text of an inline ADF node or of a block holding only inline nodes."""
    kind = node.get("type")
    if kind == "text":
        return str(node.get("text") or "")
    if kind == "hardBreak":
        return "\n"
    attrs = node.get("attrs") or {}
    if kind in {"mention", "emoji", "status"}:
        return str(attrs.get("text") or attrs.get("shortName") or "")
    if kind in {"inlineCard", "blockCard"}:
        return str(attrs.get("url") or "")
    if kind == "date":
        return str(attrs.get("timestamp") or "")
    return "".join(_adf_inline(child) for child in _adf_children(node))


def _timestamp(value: Any) -> datetime | None:
    """Parse a Jira timestamp (``2026-06-05T10:00:00.000-0400``); ``None`` if absent or odd."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _name(value: Any) -> str:
    return str(value.get("name") or "") if isinstance(value, dict) else ""


def _display_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("displayName") or value.get("name") or value.get("key") or "")


def _api_url(data: Mapping[str, Any]) -> str | None:
    self_url = data.get("self")
    return self_url if isinstance(self_url, str) and self_url else None


def _browser_url(data: Mapping[str, Any]) -> str:
    self_url = data.get("self")
    key = data.get("key")
    if isinstance(self_url, str) and "/rest/api/" in self_url and isinstance(key, str):
        base = self_url.split("/rest/api/", 1)[0]
        return f"{base}/browse/{key}"
    return self_url if isinstance(self_url, str) else ""


def browse_url(base_url: str | None, key: str) -> str | None:
    """The browser URL of issue ``key`` on the Jira at ``base_url``."""
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/browse/{key}"
