"""scripts/upgrade_smoke.py: the sources it builds from the examples (CI's wheel-matrix runs it)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import upgrade_smoke


def test_the_sources_are_the_examples_with_a_removal_between_shelf_releases(
    tmp_path: Path,
) -> None:
    made = upgrade_smoke.sources(tmp_path)
    shelf_1 = (made["shelf-1"] / "src" / "untaped_shelf" / "api.py").read_text(encoding="utf-8")
    shelf_3 = (made["shelf-3"] / "src" / "untaped_shelf" / "api.py").read_text(encoding="utf-8")
    provider = made["library-1"] / "src" / "untaped_library" / "providers" / "shelf.py"
    assert "def book_titled(" in shelf_1
    assert "book_titled" not in shelf_3
    assert "book_titled(item.name, item.pages)" in provider.read_text(encoding="utf-8")
    for source in (shelf_1, provider.read_text(encoding="utf-8")):
        ast.parse(source)
    assert 'version = "1.0.0"' in (made["shelf-1"] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "3.0.0"' in (made["shelf-3"] / "pyproject.toml").read_text(encoding="utf-8")
    library = (made["library-1"] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"untaped-shelf>=1.0"' in library


def test_an_example_it_no_longer_fits_fails_loudly() -> None:
    with pytest.raises(ValueError, match="update the upgrade smoke"):
        upgrade_smoke.calling_book_titled("from untaped_shelf.api import Book\n")
    with pytest.raises(ValueError, match="no single requirement"):
        upgrade_smoke.requiring("dependencies = []\n", "untaped-shelf", "1.0")


def test_a_failure_is_one_line_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def drifted(work: Path) -> dict[str, Path]:
        raise ValueError("the library's provider changed: update the upgrade smoke")

    monkeypatch.setattr(upgrade_smoke, "sources", drifted)
    assert upgrade_smoke.main([str(tmp_path / "core.whl"), str(tmp_path / "work")]) == 1
    assert capsys.readouterr().err.splitlines() == [
        "upgrade smoke: the library's provider changed: update the upgrade smoke"
    ]
