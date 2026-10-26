"""What the jira tests share: the fake Jira's address and the result of one run."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from untaped.sdk.testing import Ran

BASE = "https://jira.example.com"


@dataclass(frozen=True)
class Result:
    """What one ``untaped jira`` run did, its stderr as one text."""

    exit_code: int
    stdout: str
    stderr: str
    ran: Ran

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


#: ``jira(args, input=None)``: run ``untaped jira ARGS`` (the ``jira`` fixture).
Invoke = Callable[..., Result]

#: The ``jira`` settings the next run uses (the ``settings`` fixture), by key.
Settings = dict[str, Any]
