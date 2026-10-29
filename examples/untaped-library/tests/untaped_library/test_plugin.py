"""The example provider as a whole, tested the way any third-party plugin would be.

``install(Library)`` also composes ``shelf``, the owner library requires, so
the owner's commands and checks run against it.
"""

from __future__ import annotations

import json
import subprocess
import sys
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


def test_shelf_asks_library_on_a_start_that_imports_no_plugin_class(install: Installer) -> None:
    """A start after the first reads the kept declaration: asking imports the contract only.

    The answer cache still knows it is shelf's, so the answer is kept in
    shelf's folder although shelf's plugin class was never imported.
    """
    code = (
        "import sys; from untaped.sdk.contracts import Ok, gather; "
        "from untaped.sdk.contracts._declare import contract_of; "
        "from untaped.sdk.testing import compose_with; from untaped_shelf.api import BookSource\n"
        "with compose_with():\n"
        "    answers = gather(BookSource.books)()\n"
        "    cache = contract_of(BookSource).methods['books'].cache\n"
        "    print([type(a).__name__ for a in answers], cache.plugin, cache.name, "
        "'untaped_shelf.plugin' in sys.modules)"
    )
    install(Library, settings=LibrarySettings(volumes=[VolumeEntry(shelf_mark="A1", name="Dune")]))
    subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    warm = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert warm.stdout.split() == ["['Ok']", "shelf", "book_source_books", "False"]


def test_library_is_listed_filling_shelf(install: Installer) -> None:
    listed = install(Library).cli("plugin list --format json")
    assert {row["name"]: row["fills"] for row in json.loads(listed.stdout)}["library"] == ["shelf"]
