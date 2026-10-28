"""Fixtures for lint's tests: tmp packages on ``sys.path``, and a run of lint."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from textwrap import dedent

import pytest

from untaped.sdk.testing import install
from untaped_lint.plugin import LintPlugin


@pytest.fixture
def site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[dict[str, str]], Path]]:
    """Write files under ``tmp_path/site`` (on ``sys.path``); unload their modules after."""
    root = tmp_path / "site"
    root.mkdir()
    monkeypatch.syspath_prepend(str(root))
    packages: set[str] = set()

    def write(files: dict[str, str]) -> Path:
        for name, body in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dedent(body).lstrip(), encoding="utf-8")
            packages.add(Path(name).parts[0])
        importlib.invalidate_caches()
        return root

    yield write
    for module in [name for name in sys.modules if name.split(".")[0] in packages]:
        del sys.modules[module]


@pytest.fixture
def lint_run() -> Iterator[None]:
    """A run of lint for code called outside a command: the lint file's path is lint's."""
    with install(LintPlugin).running():
        yield
