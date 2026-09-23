"""Domain row models for Jira CLI output."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from untaped.capability_api import OutcomeRecord, UtcTimestamp

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


_ROW_CONFIG = ConfigDict(frozen=True, extra="ignore")


class JiraUser(BaseModel):
    """Authenticated Jira user returned by ``/myself``."""

    model_config = _ROW_CONFIG

    name: str | None = None
    key: str | None = None
    display_name: str | None = Field(
        default=None, validation_alias=AliasChoices("display_name", "displayName")
    )
    email_address: str | None = Field(
        default=None, validation_alias=AliasChoices("email_address", "emailAddress")
    )


class IssueResult(BaseModel):
    """One issue row with common nested Jira fields flattened."""

    model_config = _ROW_CONFIG

    key: str
    summary: str = ""
    status: str = ""
    assignee: str = ""
    updated_at: UtcTimestamp | None = None
    url: str = ""
    api_url: str | None = None

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
            "updated_at": _timestamp(fields.get("updated")),
            "url": _browser_url(data),
            "api_url": _api_url(data),
        }
        return {**data, **patch}


class IssueDetailResult(IssueResult):
    """One issue with the extra fields shown by ``issues get``."""

    issue_type: str = ""
    priority: str = ""
    reporter: str = ""
    labels: list[str] = Field(default_factory=list)
    created_at: UtcTimestamp | None = None
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
            "issue_type": _name(fields.get("issuetype")),
            "priority": _name(fields.get("priority")),
            "reporter": _display_name(fields.get("reporter")),
            "labels": [str(label) for label in labels] if isinstance(labels, list) else [],
            "created_at": _timestamp(fields.get("created")),
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


def _api_url(data: dict[str, Any]) -> str | None:
    self_url = data.get("self")
    return self_url if isinstance(self_url, str) and self_url else None


def _browser_url(data: dict[str, Any]) -> str:
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


class IssueOutcome(OutcomeRecord):
    """The result of one issue mutation (``jira.issue_outcome``).

    ``action`` is ``created``, ``updated``, ``commented``, ``transitioned``,
    or ``planned`` under ``--dry-run`` (where a new issue has no ``key`` yet).
    """

    key: str | None = None
    id: str | None = None
    url: str | None = None
    api_url: str | None = None
    transition_id: str | None = None
    comment_id: str | None = None


class TransitionResult(BaseModel):
    """One available Jira workflow transition."""

    model_config = _ROW_CONFIG

    id: str
    name: str


class ProjectResult(BaseModel):
    """One Jira project lookup row."""

    model_config = _ROW_CONFIG

    key: str
    name: str = ""
    id: str = ""
    project_type_key: str | None = Field(
        default=None, validation_alias=AliasChoices("project_type_key", "projectTypeKey")
    )


class BoardResult(BaseModel):
    """One Jira Software board row."""

    model_config = _ROW_CONFIG

    id: int
    name: str = ""
    type: str = ""
    api_url: str | None = Field(default=None, validation_alias=AliasChoices("api_url", "self"))


class SprintResult(BaseModel):
    """One Jira Software sprint row."""

    model_config = _ROW_CONFIG

    id: int
    name: str = ""
    state: str = ""
    start_at: UtcTimestamp | None = Field(
        default=None, validation_alias=AliasChoices("start_at", "startDate")
    )
    end_at: UtcTimestamp | None = Field(
        default=None, validation_alias=AliasChoices("end_at", "endDate")
    )
    goal: str | None = None
    origin_board_id: int | None = Field(
        default=None, validation_alias=AliasChoices("origin_board_id", "originBoardId")
    )

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: Any) -> datetime | None:
        return _timestamp(value)
