"""The jira capability follows the command, message and structure conventions."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.testing import check_conventions

pytestmark = pytest.mark.usefixtures("fresh_composition")


def test_jira_follows_the_conventions() -> None:
    check_conventions("jira", tests_dir=Path(__file__).resolve().parents[1], candidates=[])
