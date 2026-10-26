"""The example provider as a whole, tested the way any third-party plugin would be.

``install(Library)`` also composes ``shelf``, the owner library declares
under its ``shelf`` extra, so the owner's commands and checks run against it.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from untaped_library.plugin import Library
from untaped_library.settings import LibrarySettings, VolumeEntry

from untaped.sdk.testing import Install, assert_surface, check
from untaped_lint.testing import assert_clean

Installer = Callable[..., Install]


def test_library_passes_lint() -> None:
    assert_clean("library")


def test_library_passes_its_plugin_check_with_the_owner_s_conformance(install: Installer) -> None:
    volumes = [VolumeEntry(shelf_mark="A1", name="Dune"), VolumeEntry(shelf_mark="B2", name="Emma")]
    check(install(Library, settings=LibrarySettings(volumes=volumes)))


def test_library_keeps_its_surface() -> None:
    assert_surface(Library)  # snapshots/surface.txt beside this file


def test_library_is_listed_filling_shelf(install: Installer) -> None:
    listed = install(Library).cli("plugin list --format json")
    assert {row["name"]: row["fills"] for row in json.loads(listed.stdout)}["library"] == ["shelf"]
