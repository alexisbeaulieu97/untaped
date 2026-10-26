"""A run for tests of blocks a plugin class holds: one package owned by one plugin."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.sdk._run import Composition, Run


def run_owning(home: Path, package: str, plugin: str) -> Run:
    """A run whose composition gives ``package``'s classes to ``plugin``; no settings, no sink."""
    return Run(
        locate_home=lambda: home,
        composition=Composition(packages={package: plugin}),
        picks=lambda _section, model: model(),
        diag=None,  # type: ignore[arg-type]  # these tests write nothing to stderr
        terminal=lambda: pytest.fail("no terminal in this test"),
    )
