"""Fixtures for the convention-check tests: a fresh composition and tmp packages."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from textwrap import dedent

import pytest

#: Writes ``{relative path: source}`` under an importable directory and returns it.
Install = Callable[[dict[str, str]], Path]


@pytest.fixture(autouse=True)
def _composition_is_forgotten(fresh_composition: None) -> None:
    """Every test here composes the root; forget it afterwards."""


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Install]:
    """Write files under ``tmp_path/site`` (on ``sys.path``); unload their modules after."""
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.syspath_prepend(str(site))
    packages: set[str] = set()

    def write(files: dict[str, str]) -> Path:
        for name, body in files.items():
            path = site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dedent(body).lstrip(), encoding="utf-8")
            packages.add(Path(name).parts[0])
        importlib.invalidate_caches()
        return site

    yield write
    for module in [name for name in sys.modules if name.split(".")[0] in packages]:
        del sys.modules[module]
