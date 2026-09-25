"""Parsing graph roots from bare stdin lines and pipe records."""

from __future__ import annotations

import pytest

from untaped.capabilities.ansible.domain.graph_roots import (
    GraphRoot,
    root_from_line,
    root_from_record,
)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("acme/app", GraphRoot(target="acme/app")),
        ("acme/app@main", GraphRoot(target="acme/app", ref="main")),
        ("acme/app@feature/x", GraphRoot(target="acme/app", ref="feature/x")),
        ("  acme/app@v1.2.0  ", GraphRoot(target="acme/app", ref="v1.2.0")),
        ("git@github.com:acme/app.git", GraphRoot(target="git@github.com:acme/app.git")),
        ("https://github.com/acme/app", GraphRoot(target="https://github.com/acme/app")),
    ],
)
def test_root_from_line(line: str, expected: GraphRoot) -> None:
    assert root_from_line(line) == expected


def test_root_from_record_prefers_scm_fields_and_treats_empty_ref_as_default() -> None:
    record = {"scm_url": "https://github.com/acme/app.git", "effective_scm_ref": "", "ref": "x"}
    assert root_from_record(record) == GraphRoot(target="https://github.com/acme/app.git")
    assert root_from_record({"full_name": "acme/app", "ref": "v1"}) == GraphRoot(
        target="acme/app", ref="v1"
    )
    assert root_from_record({"id": 1, "scm_branch": "main"}) is None
