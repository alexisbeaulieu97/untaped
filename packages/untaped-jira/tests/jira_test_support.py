"""What the jira tests share: the fake Jira's address, its recorded answers, one run's result."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from untaped.sdk.storage import safe_path_segment
from untaped.sdk.testing import Ran
from untaped_jira.server import JiraVersion

BASE = "https://jira.example.com"

#: One folder per recorded version (``safe_path_segment(str(version))``) of Jira's answers.
RECORDED_DIR = Path(__file__).parent / "untaped_jira" / "recorded"


def recorded_answer(version: JiraVersion, name: str) -> Any:
    """``name``'s recorded answer (``recorded/<version>/<name>.json``), as JSON."""
    path = RECORDED_DIR / safe_path_segment(str(version)) / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


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
