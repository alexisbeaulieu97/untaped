"""The awx capability follows the command, message and structure conventions."""

from __future__ import annotations

from pathlib import Path

from untaped.testing import check_conventions


def test_awx_follows_the_conventions() -> None:
    check_conventions("awx", tests_dir=Path(__file__).resolve().parents[1], externals=[])
