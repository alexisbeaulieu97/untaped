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
    "issuelinks",
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
        patch = {
            "summary": _text(fields.get("summary")),
            "status": _name(fields.get("status")),
            "assignee": _display_name(fields.get("assignee")),
            "updated_at": _timestamp(fields.get("updated")),
            "url": _browser_url(data),
            "api_url": _api_url(data),
        }
        return {**data, **patch}


class CommentResult(BaseModel):
    """One issue comment (``jira.comment``)."""

    model_config = _ROW_CONFIG

    id: str
    issue_key: str = ""
    author: str = ""
    created_at: UtcTimestamp | None = None
    updated_at: UtcTimestamp | None = None
    body: str = ""
    api_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_jira_comment(cls, data: Any) -> Any:
        """Flatten a raw Jira comment (nested ``author``, ``created``, ``self``)."""
        if not isinstance(data, dict):
            return data
        raw = {"self", "created", "updated"} & data.keys() or isinstance(data.get("author"), dict)
        if not raw:
            return data
        patch = {
            "id": str(data.get("id") or ""),
            "author": _display_name(data.get("author")),
            "created_at": _timestamp(data.get("created")),
            "updated_at": _timestamp(data.get("updated")),
            "body": _text(data.get("body")),
            "api_url": _api_url(data),
        }
        return {**data, **patch}


class IssueLink(BaseModel):
    """One link on an issue, read from the issue's own ``issuelinks`` field.

    ``direction`` says which side of the link this issue is on: ``outward``
    when this issue is the link's source (Jira shows the type's outward
    phrase, e.g. ``blocks``), ``inward`` when it is the target (the inward
    phrase, e.g. ``is blocked by``). ``relation`` is that phrase verbatim.
    The linked issue carries only what the link embeds; it is not fetched.
    """

    model_config = _ROW_CONFIG

    key: str
    summary: str = ""
    status: str = ""
    type: str = ""
    direction: str = ""
    relation: str = ""
    url: str = ""

    @classmethod
    def from_jira(cls, link: Any) -> IssueLink | None:
        """Build a row from one raw ``issuelinks`` entry; ``None`` if malformed."""
        if not isinstance(link, dict):
            return None
        raw_type = link.get("type")
        link_type: dict[str, Any] = raw_type if isinstance(raw_type, dict) else {}
        for direction in ("outward", "inward"):
            other = link.get(f"{direction}Issue")
            if isinstance(other, dict) and isinstance(other.get("key"), str):
                fields = other.get("fields") or {}
                return cls(
                    key=other["key"],
                    summary=_text(fields.get("summary")),
                    status=_name(fields.get("status")),
                    type=str(link_type.get("name") or ""),
                    direction=direction,
                    relation=str(link_type.get(direction) or ""),
                    url=_browser_url(other),
                )
        return None


class IssueDetailResult(IssueResult):
    """One issue with the extra fields shown by ``issues get``.

    ``comments`` is ``None`` unless ``issues get --comments`` fetched them.
    """

    issue_type: str = ""
    priority: str = ""
    reporter: str = ""
    labels: list[str] = Field(default_factory=list)
    created_at: UtcTimestamp | None = None
    resolution: str = ""
    description: str = ""
    links: list[IssueLink] = Field(default_factory=list)
    comments: list[CommentResult] | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_detail_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        fields = data.get("fields") or {}
        labels = fields.get("labels") or []
        patch = {
            "issue_type": _name(fields.get("issuetype")),
            "priority": _name(fields.get("priority")),
            "reporter": _display_name(fields.get("reporter")),
            "labels": [str(label) for label in labels] if isinstance(labels, list) else [],
            "created_at": _timestamp(fields.get("created")),
            "resolution": _name(fields.get("resolution")),
            "description": _text(fields.get("description")),
            "links": _links(fields.get("issuelinks")),
        }
        return {**data, **patch}


def _links(value: Any) -> list[IssueLink]:
    if not isinstance(value, list):
        return []
    return [link for raw in value if (link := IssueLink.from_jira(raw)) is not None]


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
    ``linked``, or ``planned`` under ``--dry-run`` (where a new issue has no ``key`` yet).
    """

    key: str | None = None
    id: str | None = None
    url: str | None = None
    api_url: str | None = None
    transition_id: str | None = None
    comment_id: str | None = None
    link_type: str | None = None
    linked_key: str | None = None


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
