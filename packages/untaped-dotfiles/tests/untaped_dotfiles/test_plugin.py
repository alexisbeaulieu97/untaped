"""The dotfiles plugin as a whole: lint, its plugin check, its surface, and the root mounting it."""

from __future__ import annotations

import json
from collections.abc import Callable

from untaped.sdk.testing import Home, Install, assert_surface, check, invoke_root
from untaped_dotfiles.plugin import Dotfiles
from untaped_lint.testing import assert_clean

Installer = Callable[..., Install]


def test_dotfiles_passes_lint() -> None:
    assert_clean("dotfiles")


def test_dotfiles_passes_its_plugin_check(install: Installer) -> None:
    check(install(Dotfiles))


def test_dotfiles_keeps_its_surface() -> None:
    assert_surface(Dotfiles)  # snapshots/surface.txt beside this file


def test_the_root_mounts_the_plugin() -> None:
    listed = invoke_root(["plugin", "list", "--format", "json"])
    assert ("dotfiles", "ready") in [(r["name"], r["status"]) for r in json.loads(listed.stdout)]
    assert invoke_root(["dotfiles", "repos"]).exit_code == 0


def test_the_folders_follow_the_home_when_unset(install: Installer, home: Home) -> None:
    with install(Dotfiles).running():
        folders = [Dotfiles.clones.path, Dotfiles.kept.path, Dotfiles.prompt.path]
    assert folders == [home.plugin(Dotfiles) / name for name in ("clones", "kept", "prompt")]
