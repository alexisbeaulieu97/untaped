"""Lint's one glob: ``*`` stops at ``/``, ``**`` crosses it."""

from __future__ import annotations

import pytest

from untaped_lint.domain.glob import matches


@pytest.mark.parametrize(
    ("pattern", "text", "expected"),
    [
        ("JobTemplate/*/legacy-*", "JobTemplate/Platform/legacy-web", True),
        ("JobTemplate/*/legacy-*", "JobTemplate/Platform/web/legacy-x", False),
        ("tests/**", "tests/a/b.py", True),
        ("tests/**", "src/tests/a.py", False),
        ("*", ".hidden", True),
        ("a?c", "abc", True),
    ],
)
def test_globs_read_the_way_they_look(pattern: str, text: str, expected: bool) -> None:
    assert matches(pattern, text) is expected
