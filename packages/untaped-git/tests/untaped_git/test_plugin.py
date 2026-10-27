"""The git plugin as a whole: lint, its plugin check, its surface, and the root mounting it."""

from __future__ import annotations

import json
from collections.abc import Callable

from untaped.sdk.testing import Install, assert_surface, check, invoke_root
from untaped_git.plugin import Git
from untaped_lint.testing import assert_clean

Installer = Callable[..., Install]


def test_git_passes_lint() -> None:
    assert_clean("git")


def test_git_passes_its_plugin_check(install: Installer) -> None:
    check(install(Git))


def test_git_keeps_its_surface() -> None:
    assert_surface(Git)  # snapshots/surface.txt beside this file


def test_the_root_mounts_the_plugin() -> None:
    listed = invoke_root(["plugin", "list", "--format", "json"])
    assert ("git", "ready") in [(r["name"], r["status"]) for r in json.loads(listed.stdout)]
    assert invoke_root(["git", "store"]).exit_code == 0
