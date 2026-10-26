"""Jira issue payload helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import ConfigError


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
    if not isinstance(raw_fields, dict):
        raise ConfigError("Jira payload `fields` must be an object", category="invalid")
    if "update" in payload and not isinstance(payload["update"], dict):
        raise ConfigError("Jira payload `update` must be an object", category="invalid")
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


def build_transition_payload(
    transition_id: str,
    *,
    comment: str | None = None,
    resolution: str | None = None,
) -> dict[str, Any]:
    """The body of ``POST issue/{key}/transitions``, with an optional comment and resolution."""

    payload: dict[str, Any] = {"transition": {"id": transition_id}}
    if resolution is not None:
        payload["fields"] = {"resolution": {"name": resolution}}
    if comment is not None:
        payload["update"] = {"comment": [{"add": {"body": comment}}]}
    return payload


def build_assignee_payload(username: str | None) -> dict[str, Any]:
    """The body of ``PUT issue/{key}/assignee``; ``None`` unassigns the issue."""

    return {"name": username}


def build_link_payload(key: str, link_type: str, other: str) -> dict[str, Any]:
    """The body of ``POST issueLink``: ``key`` <link_type's outward phrase> ``other``.

    Jira names the issue that shows the outward phrase (``blocks``) the
    ``inwardIssue``, so ``OPS-1 Blocks OPS-2`` makes OPS-1 block OPS-2.
    """

    return {
        "type": {"name": link_type},
        "inwardIssue": {"key": key},
        "outwardIssue": {"key": other},
    }


class IssueDocument(BaseModel):
    """A Jira-shaped issue document, as Jira's REST API takes one.

    ``fields`` sets fields by id (``summary``, ``labels``, ``customfield_10010``);
    ``update`` lists operations per field (``{"labels": [{"add": "infra"}]}``).
    Flags given beside the file override its fields.
    """

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any] = Field(default_factory=dict)
    update: dict[str, Any] = Field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        """The document as a request body: ``update`` only when the file has one."""
        return self.model_dump(exclude_unset=True)
