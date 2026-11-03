"""A user's Jira-shaped fields document, and the flags laid over it."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def build_issue_payload(
    *,
    base: dict[str, Any] | None = None,
    project: str | None = None,
    issue_type: str | None = None,
    summary: str | None = None,
    description: str | None = None,
    fields: dict[str, str] | None = None,
    json_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge a Jira-shaped base payload with CLI convenience overlays."""

    payload = deepcopy(base or {})
    raw_fields = payload.setdefault("fields", {})
    if project is not None:
        raw_fields["project"] = {"key": project}
    if issue_type is not None:
        raw_fields["issuetype"] = {"name": issue_type}
    if summary is not None:
        raw_fields["summary"] = summary
    if description is not None:
        raw_fields["description"] = description
    raw_fields.update(fields or {})
    raw_fields.update(json_fields or {})
    return payload


class IssueDocument(BaseModel):
    """A Jira-shaped issue document, as Jira's REST API takes one.

    ``fields`` sets fields by id (``summary``, ``labels``, ``customfield_10010``);
    ``update`` lists operations per field (``{"labels": [{"add": "infra"}]}``).
    Other top-level keys Jira takes (``properties``, ``historyMetadata``) are
    sent as given. Flags given beside the file override its fields.
    """

    model_config = ConfigDict(extra="allow")

    fields: dict[str, Any] = Field(default_factory=dict)
    update: dict[str, Any] = Field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        """The document as a request body: ``update`` only when the file has one."""
        return self.model_dump(exclude_unset=True)
