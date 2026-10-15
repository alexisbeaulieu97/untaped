"""Fixtures for lint's tests: tmp packages on ``sys.path``."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.fixture
def install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[dict[str, str]], Path]]:
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
