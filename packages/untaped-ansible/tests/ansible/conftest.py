"""Fixtures every ansible test can use."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from untaped.sdk.testing import compose_with
from untaped_ansible import SPEC as ANSIBLE
from untaped_git.plugin import Git


@pytest.fixture
def composed() -> Iterator[None]:
    """A run composing git and ansible, as an invocation has: the repo store reads git's
    settings from it."""
    with compose_with(Git, ANSIBLE):
        yield
