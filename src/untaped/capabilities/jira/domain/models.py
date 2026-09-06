"""Domain row models for Jira CLI output."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
