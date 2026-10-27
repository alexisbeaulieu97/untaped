"""The workspace plugin as a whole: lint, its plugin check, its surface, the root mounting it."""

from __future__ import annotations

import json
from collections.abc import Callable

from untaped.sdk.testing import Install, assert_surface, check, invoke_root
from untaped_lint.testing import assert_clean
from untaped_workspace.plugin import WorkspacePlugin

Installer = Callable[..., Install]


def test_workspace_passes_lint() -> None:
    assert_clean("workspace")


def test_workspace_passes_its_plugin_check(install: Installer) -> None:
    check(install(WorkspacePlugin))


def test_workspace_keeps_its_surface() -> None:
    assert_surface(WorkspacePlugin)  # snapshots/surface.txt beside this file


def test_the_root_mounts_the_plugin() -> None:
    listed = invoke_root(["plugin", "list", "--format", "json"])
    assert ("workspace", "ready") in [(r["name"], r["status"]) for r in json.loads(listed.stdout)]
    assert invoke_root(["workspace", "list"]).exit_code == 0
