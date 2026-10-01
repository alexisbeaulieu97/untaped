"""The ansible capability follows the command, message and structure conventions."""

from __future__ import annotations

from pathlib import Path

from untaped.testing import check_conventions


def test_ansible_follows_the_conventions() -> None:
    check_conventions("ansible", tests_dir=Path(__file__).resolve().parents[1])
