"""The github plugin as a whole: lint, its plugin check, its surface, the root mounting it."""

from __future__ import annotations

import json
from collections.abc import Callable

from untaped.sdk.testing import Install, assert_surface, check, invoke_root
from untaped_github.plugin import Github
from untaped_lint.testing import assert_clean

Installer = Callable[..., Install]


def test_github_passes_lint() -> None:
    assert_clean("github")


def test_github_passes_its_plugin_check(install: Installer) -> None:
    check(install(Github))


def test_github_keeps_its_surface() -> None:
    assert_surface(Github)  # snapshots/surface.txt beside this file


def test_the_root_mounts_the_plugin() -> None:
    listed = invoke_root(["plugin", "list", "--format", "json"])
    assert ("github", "ready") in [(r["name"], r["status"]) for r in json.loads(listed.stdout)]
    assert "sweep" in invoke_root(["github", "--help"]).stdout
