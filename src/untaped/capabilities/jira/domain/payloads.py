"""Jira issue payload helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from untaped.api import ConfigError


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
        raise ConfigError("Jira payload `fields` must be an object")
    if "update" in payload and not isinstance(payload["update"], dict):
        raise ConfigError("Jira payload `update` must be an object")
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
