"""Doctor's ``plugin-uses`` rows: the deprecated names a plugin uses, kept by its declaration."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from core_test_support.runtime.plugin_uses_site import FILLS_BOOKS, Site, installed_site
from untaped.runtime import bootstrap
from untaped.runtime.registry import compose
from untaped.sdk.testing import CliInvoker


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    with installed_site(tmp_path, monkeypatch) as installed:
        yield installed


def _uses_rows(site: Site, report: str) -> list[dict[str, Any]]:
    found = [site.acme(report, requires=("untaped-shelf>=1.0",)), site.shelf()]
    compose(found)  # composing keeps what it found; doctor's start reads it back
    root = bootstrap.build_root_app(candidates=found)
    result = CliInvoker().invoke(root.meta, ["doctor", "--format", "json"])
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return [row for row in rows if row["check"] == "plugin-uses"]


def test_one_row_per_plugin_and_owner_names_each_use_and_what_its_removal_does(
    site: Site,
) -> None:
    report = FILLS_BOOKS.replace(
        "from untaped_shelf.api import Book, BookSource",
        "from untaped_shelf.api import Book, BookSource, human_size",
    )
    [row] = _uses_rows(site, report)
    assert (row["plugin"], row["status"], row["title"], row["fix"]) == (
        "acme",
        "warn",
        "deprecated",
        None,
    )
    assert row["detail"] == (
        "untaped-acme 1.0 imports untaped_shelf.api.human_size (untaped_acme/report.py:3), "
        "removed in untaped-shelf 5.0: upgrading untaped-shelf to 5.0 disables acme unless a "
        "newer untaped-acme stops using it; "
        "untaped-acme 1.0 fills shelf.book_source.books (untaped_acme.report:Shelved.books), "
        "removed in untaped-shelf 5.0: upgrading untaped-shelf to 5.0 disables acme unless a "
        "newer untaped-acme stops using it"
    )


def test_a_plugin_using_nothing_deprecated_gets_no_row(site: Site) -> None:
    assert _uses_rows(site, "from untaped_shelf.api import size_text\n") == []
