"""Domain row models for Jira CLI output."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Jira ``fields`` requested for list rows (search) and the richer single-issue
# detail view (get); the client requests exactly what the models flatten.
ISSUE_ROW_FIELDS: tuple[str, ...] = ("summary", "status", "assignee", "updated")
ISSUE_DETAIL_FIELDS: tuple[str, ...] = (
    *ISSUE_ROW_FIELDS,
    "issuetype",
    "priority",
    "reporter",
    "labels",
    "created",
    "resolution",
    "description",
)


class JiraUser(BaseModel):
    """Authenticated Jira user returned by ``/myself``."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    key: str | None = None
    displayName: str | None = None
    emailAddress: str | None = None


class IssueResult(BaseModel):
    """One issue row with common nested Jira fields flattened."""

    model_config = ConfigDict(extra="ignore")

    key: str
    summary: str = ""
    status: str = ""
    assignee: str = ""
    updated: str = ""
    url: str = ""

    @model_validator(mode="before")
    @classmethod
    def _flatten_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        fields = data.get("fields") or {}
        if not isinstance(fields, dict):
            fields = {}
        status = fields.get("status") or {}
        assignee = fields.get("assignee") or {}
        patch = {
            "summary": _text(fields.get("summary")),
            "status": status.get("name", "") if isinstance(status, dict) else "",
            "assignee": _display_name(assignee),
            "updated": _text(fields.get("updated")),
            "url": _browser_url(data),
        }
        return {**data, **patch}


class IssueDetailResult(IssueResult):
    """One issue with the extra fields shown by ``issue get``."""

    issuetype: str = ""
    priority: str = ""
    reporter: str = ""
    labels: list[str] = Field(default_factory=list)
    created: str = ""
    resolution: str = ""
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def _flatten_detail_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        fields = data.get("fields") or {}
        if not isinstance(fields, dict):
            fields = {}
        labels = fields.get("labels") or []
        patch = {
            "issuetype": _name(fields.get("issuetype")),
            "priority": _name(fields.get("priority")),
            "reporter": _display_name(fields.get("reporter")),
            "labels": [str(label) for label in labels] if isinstance(labels, list) else [],
            "created": _text(fields.get("created")),
            "resolution": _name(fields.get("resolution")),
            "description": _text(fields.get("description")),
        }
        return {**data, **patch}


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
    attrs = node.get("attrs")
    if not isinstance(attrs, dict):
        attrs = {}
    if kind in {"mention", "emoji", "status"}:
        return str(attrs.get("text") or attrs.get("shortName") or "")
    if kind in {"inlineCard", "blockCard"}:
        return str(attrs.get("url") or "")
    if kind == "date":
        return str(attrs.get("timestamp") or "")
    return "".join(_adf_inline(child) for child in _adf_children(node))


def _name(value: Any) -> str:
    return str(value.get("name") or "") if isinstance(value, dict) else ""


def _display_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("displayName") or value.get("name") or value.get("key") or "")


def _browser_url(data: dict[str, Any]) -> str:
    self_url = data.get("self")
    key = data.get("key")
    if isinstance(self_url, str) and "/rest/api/" in self_url and isinstance(key, str):
        base = self_url.split("/rest/api/", 1)[0]
        return f"{base}/browse/{key}"
    return self_url if isinstance(self_url, str) else ""


class IssueMutationResult(BaseModel):
    """One row emitted after an issue mutation command."""

    model_config = ConfigDict(extra="ignore")

    key: str
    id: str | None = None
    self: str | None = None
    status: str = "ok"
    transition_id: str | None = None


class CommentResult(BaseModel):
    """One row emitted after adding a comment."""

    model_config = ConfigDict(extra="ignore")

    id: str
    issue: str = ""


class TransitionResult(BaseModel):
    """One available Jira workflow transition."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str


class ProjectResult(BaseModel):
    """One Jira project lookup row."""

    model_config = ConfigDict(extra="ignore")

    key: str
    name: str = ""
    id: str = ""
    projectTypeKey: str | None = None


class BoardResult(BaseModel):
    """One Jira Software board row."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str = ""
    type: str = ""
    self: str | None = None


class SprintResult(BaseModel):
    """One Jira Software sprint row."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str = ""
    state: str = ""
    startDate: str | None = None
    endDate: str | None = None
    goal: str | None = None
    originBoardId: int | None = Field(default=None)
