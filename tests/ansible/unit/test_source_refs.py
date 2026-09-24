"""Tests for effective source ref selection."""

from __future__ import annotations

from typing import Any

import pytest

from untaped.capabilities.ansible.application.source_refs import source_ref_selections
from untaped.capabilities.ansible.settings import SourceDefinition


@pytest.mark.parametrize(
    ("source", "scan_default", "expected"),
    [
        ({}, "all", [("heads", ("*",), ("heads",)), ("tags", ("*",), ("tags",))]),
        ({}, "default_branch", [("heads", ("master",), ("heads/master",))]),
        # patterns without a kind apply to heads and tags
        (
            {"ref_patterns": ["v3"]},
            "all",
            [("heads", ("v3",), ("heads/v3",)), ("tags", ("v3",), ("tags/v3",))],
        ),
        ({"ref_kinds": ["tags"]}, "all", [("tags", ("*",), ("tags",))]),
        (
            {"ref_kinds": ["heads"], "ref_patterns": ["release/*"]},
            "all",
            [("heads", ("release/*",), ("heads/release/",))],
        ),
    ],
)
def test_source_ref_selections(
    source: dict[str, Any], scan_default: Any, expected: list[tuple[Any, ...]]
) -> None:
    selections = source_ref_selections(
        SourceDefinition(name="prod", repos=["acme/site"], **source),
        default_branch="master",
        ref_scan_default=scan_default,
    )

    assert [(item.kind, item.patterns, item.namespaces) for item in selections] == expected
