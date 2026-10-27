"""Shared fixtures for the ansible plugin tests.

Every test runs with ``GH_TOKEN`` set and ``ansible.index_dir`` at its
``tmp_path`` (:mod:`ansible_test_support`).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ansible_test_support import TOKEN, Sections, installed, using
from untaped.sdk.testing import Home, Running


@pytest.fixture(autouse=True)
def sections(home: Home, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Sections]:
    """The sections the next run writes; a test changes them before invoking."""
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    current = Sections(home=home, ansible={"index_dir": tmp_path})
    with using(current):
        yield current


@pytest.fixture
def composed(sections: Sections) -> Iterator[Running]:
    """A run composing git, github and ansible, as an invocation has: the repo store reads git's
    settings from it, and its ``http`` builds the clients a command's ``self.ctx.http`` would."""
    del sections  # written by now, as a command's run reads them
    with installed().running() as running:
        yield running
