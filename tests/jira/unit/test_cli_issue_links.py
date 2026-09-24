"""``jira issues get`` reports the issue's links from its own ``issuelinks``."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
import yaml

from untaped.capabilities.jira.cli import app
from untaped.testing import CliInvoker, CliResult

BASE = "https://jira.example.com"

_BLOCKS = {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"}
_DUPLICATE = {"name": "Duplicate", "inward": "is duplicated by", "outward": "duplicates"}
_RELATES = {"name": "Relates", "inward": "relates to", "outward": "relates to"}


def _linked(key: str, summary: str, status: str) -> dict[str, Any]:
    return {
        "id": key.rsplit("-", 1)[-1],
        "key": key,
        "self": f"{BASE}/rest/api/2/issue/{key.rsplit('-', 1)[-1]}",
        "fields": {"summary": summary, "status": {"name": status}},
    }


def _get(links: list[dict[str, Any]] | None, *args: str) -> tuple[CliResult, Any]:
    fields: dict[str, Any] = {"summary": "Fix deploy", "status": {"name": "In Progress"}}
    if links is not None:
        fields["issuelinks"] = links
    payload = {"key": "ABC-1", "self": f"{BASE}/rest/api/2/issue/10001", "fields": fields}
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/rest/api/2/issue/ABC-1").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = CliInvoker().invoke(app, ["issues", "get", "ABC-1", *args])
    assert result.exit_code == 0, result.output + result.stderr
    return result, route


def test_no_links_is_an_empty_list() -> None:
    result, route = _get(None, "--format", "json")

    assert json.loads(result.stdout)["links"] == []
    assert "issuelinks" in route.calls[0].request.url.params["fields"].split(",")
    # Only the issue itself is read; linked issues are never fetched.
    assert len(route.calls) == 1


def test_outward_and_inward_links_carry_their_direction_phrase() -> None:
    links = [
        {"id": "1", "type": _BLOCKS, "outwardIssue": _linked("ABC-2", "Release 2.0", "To Do")},
        {"id": "2", "type": _BLOCKS, "inwardIssue": _linked("OPS-7", "Upgrade runners", "Done")},
    ]

    result, _ = _get(links, "--format", "json")

    assert json.loads(result.stdout)["links"] == [
        {
            "key": "ABC-2",
            "summary": "Release 2.0",
            "status": "To Do",
            "type": "Blocks",
            "direction": "outward",
            "relation": "blocks",
            "url": f"{BASE}/browse/ABC-2",
        },
        {
            "key": "OPS-7",
            "summary": "Upgrade runners",
            "status": "Done",
            "type": "Blocks",
            "direction": "inward",
            "relation": "is blocked by",
            "url": f"{BASE}/browse/OPS-7",
        },
    ]


def test_several_link_types_on_one_issue_in_yaml() -> None:
    links = [
        {"id": "1", "type": _BLOCKS, "outwardIssue": _linked("ABC-2", "Release", "To Do")},
        {"id": "2", "type": _DUPLICATE, "inwardIssue": _linked("ABC-9", "Same bug", "Closed")},
        {"id": "3", "type": _RELATES, "outwardIssue": _linked("DOC-4", "Runbook", "Open")},
        {"id": "4", "type": _RELATES},  # malformed: no linked issue
    ]

    result, _ = _get(links, "--format", "yaml")

    rows = yaml.safe_load(result.stdout)["links"]
    assert [(row["type"], row["relation"], row["key"]) for row in rows] == [
        ("Blocks", "blocks", "ABC-2"),
        ("Duplicate", "is duplicated by", "ABC-9"),
        ("Relates", "relates to", "DOC-4"),
    ]


def test_detail_table_lists_links() -> None:
    links = [
        {"id": "1", "type": _BLOCKS, "outwardIssue": _linked("ABC-2", "Release", "To Do")},
        {"id": "2", "type": _DUPLICATE, "inwardIssue": _linked("ABC-9", "Same bug", "Closed")},
    ]

    result, _ = _get(links)

    assert "links:" in result.stdout
    assert "blocks ABC-2 (To Do): Release" in result.stdout
    assert "is duplicated by ABC-9 (Closed): Same bug" in result.stdout


def test_detail_table_without_links() -> None:
    result, _ = _get([])

    assert "links:" in result.stdout
