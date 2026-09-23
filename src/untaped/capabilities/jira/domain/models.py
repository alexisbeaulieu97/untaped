"""Domain row models for Jira CLI output."""

from __future__ import annotations

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
            "summary": fields.get("summary") or "",
            "status": status.get("name", "") if isinstance(status, dict) else "",
            "assignee": _display_name(assignee),
            "updated": fields.get("updated") or "",
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
            "created": fields.get("created") or "",
            "resolution": _name(fields.get("resolution")),
            "description": fields.get("description") or "",
        }
        return {**data, **patch}


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
