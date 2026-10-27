"""Fixtures every ansible test can use."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from untaped.sdk.testing import compose_with
from untaped_ansible import SPEC as ANSIBLE
from untaped_git.plugin import Git
from untaped_github.plugin import Github


@pytest.fixture
def composed() -> Iterator[None]:
    """A run composing git, github and ansible, as an invocation has: the repo store reads
    git's settings from it, and ansible's GitHub reads reach github's token through it."""
    with compose_with(Git, Github, ANSIBLE):
        yield
