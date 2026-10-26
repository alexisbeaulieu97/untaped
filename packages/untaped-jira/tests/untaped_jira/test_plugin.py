"""The jira plugin as a whole: lint, its plugin check and its surface."""

from __future__ import annotations

from collections.abc import Callable

from untaped.sdk.testing import Install, assert_surface, check
from untaped_jira.plugin import Jira
from untaped_lint.testing import assert_clean

Installer = Callable[..., Install]


def test_jira_passes_lint() -> None:
    assert_clean("jira")


def test_jira_passes_its_plugin_check(install: Installer) -> None:
    check(install(Jira))


def test_jira_keeps_its_surface() -> None:
    assert_surface(Jira)  # snapshots/surface.txt beside this file
