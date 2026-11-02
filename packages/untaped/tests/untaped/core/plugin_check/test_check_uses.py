"""``check_plugin``'s ``uses-deprecated`` rows: each deprecated contract method a provider fills."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from core_test_support.runtime.plugin_uses_site import FILLS_BOOKS, Site, installed_site
from untaped.core.plugin_check.check import check_plugins
from untaped.runtime import bootstrap


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    with installed_site(tmp_path, monkeypatch) as installed:
        yield installed


def test_a_deprecated_fill_is_a_warning_naming_its_removal_and_replacement(site: Site) -> None:
    found = [site.acme(FILLS_BOOKS, requires=("untaped-shelf>=1.0",)), site.shelf()]
    result = bootstrap.compose_root(candidates=found)
    rows = [row for row in check_plugins(result, found, "acme") if row.title == "uses-deprecated"]
    assert [(row.check, row.title, row.status, row.detail) for row in rows] == [
        (
            "conventions",
            "uses-deprecated",
            "warn",
            "untaped_acme.report:Shelved.books: `shelf.book_source.books`, which it fills, is "
            "deprecated and will be removed in untaped-shelf 5.0; use `BookSource.all_books`",
        )
    ]


def test_a_deprecated_import_is_lint_s_to_read(site: Site) -> None:
    found = [
        site.acme("from untaped_shelf.api import human_size\n", requires=("untaped-shelf>=1.0",)),
        site.shelf(),
    ]
    result = bootstrap.compose_root(candidates=found)
    assert [
        row for row in check_plugins(result, found, "acme") if row.title == "uses-deprecated"
    ] == []
