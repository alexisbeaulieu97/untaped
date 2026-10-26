"""``jira issues get`` reports the issue's links from its own ``issuelinks``."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
import yaml

from jira_test_support import BASE, Invoke, Result

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


def _get(jira: Invoke, links: list[dict[str, Any]] | None, *args: str) -> tuple[Result, Any]:
    fields: dict[str, Any] = {"summary": "Fix deploy", "status": {"name": "In Progress"}}
    if links is not None:
        fields["issuelinks"] = links
    payload = {"key": "ABC-1", "self": f"{BASE}/rest/api/2/issue/10001", "fields": fields}
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/rest/api/2/issue/ABC-1").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = jira(["issues", "get", "ABC-1", *args])
    assert result.exit_code == 0, result.output + result.stderr
    return result, route


def test_no_links_is_an_empty_list(jira: Invoke) -> None:
    result, route = _get(jira, None, "--format", "json")

    assert json.loads(result.stdout)["links"] == []
    assert "issuelinks" in route.calls[0].request.url.params["fields"].split(",")
    # Only the issue itself is read; linked issues are never fetched.
    assert len(route.calls) == 1


def test_outward_and_inward_links_carry_their_direction_phrase(jira: Invoke) -> None:
    links = [
        {"id": "1", "type": _BLOCKS, "outwardIssue": _linked("ABC-2", "Release 2.0", "To Do")},
        {"id": "2", "type": _BLOCKS, "inwardIssue": _linked("OPS-7", "Upgrade runners", "Done")},
    ]

    result, _ = _get(jira, links, "--format", "json")

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


def test_several_link_types_on_one_issue_in_yaml(jira: Invoke) -> None:
    links = [
        {"id": "1", "type": _BLOCKS, "outwardIssue": _linked("ABC-2", "Release", "To Do")},
        {"id": "2", "type": _DUPLICATE, "inwardIssue": _linked("ABC-9", "Same bug", "Closed")},
        {"id": "3", "type": _RELATES, "outwardIssue": _linked("DOC-4", "Runbook", "Open")},
        {"id": "4", "type": _RELATES},  # malformed: no linked issue
    ]

    result, _ = _get(jira, links, "--format", "yaml")

    rows = yaml.safe_load(result.stdout)["links"]
    assert [(row["type"], row["relation"], row["key"]) for row in rows] == [
        ("Blocks", "blocks", "ABC-2"),
        ("Duplicate", "is duplicated by", "ABC-9"),
        ("Relates", "relates to", "DOC-4"),
    ]


def test_detail_table_lists_links(jira: Invoke) -> None:
    links = [
        {"id": "1", "type": _BLOCKS, "outwardIssue": _linked("ABC-2", "Release", "To Do")},
        {"id": "2", "type": _DUPLICATE, "inwardIssue": _linked("ABC-9", "Same bug", "Closed")},
    ]

    result, _ = _get(jira, links)

    # the links follow the issue as their own table, one row each
    rows = [
        line.split("│")[1:-1]
        for line in result.stdout.splitlines()
        if "ABC-2" in line or "ABC-9" in line
    ]
    assert [[cell.strip() for cell in row] for row in rows] == [
        ["ABC-2", "Release", "To Do", "blocks"],
        ["ABC-9", "Same bug", "Closed", "is duplicated by"],
    ]


def test_detail_table_hides_empty_links(jira: Invoke) -> None:
    result, _ = _get(jira, [])

    assert "links" not in result.stdout
    assert "No links." not in result.stderr


def test_links_create_help_keeps_the_outward_phrase_placeholder(jira: Invoke) -> None:
    for args in (["issues", "links", "--help"], ["issues", "links", "create", "--help"]):
        result = jira(args)

        assert result.exit_code == 0, result.output
        assert "OUTWARD-PHRASE" in result.stdout, result.stdout
