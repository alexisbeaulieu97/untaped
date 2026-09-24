from pathlib import Path

import pytest
from pydantic import ValidationError

from untaped.capabilities.workspace.domain import RepoStatus, SyncOutcome


@pytest.mark.parametrize(
    ("counts", "dirty", "diverged"),
    [
        ({}, False, False),
        ({"modified": 1}, True, False),
        ({"untracked": 1}, True, False),
        ({"ahead": 1}, False, False),
        ({"behind": 1}, False, False),
        ({"ahead": 1, "behind": 1}, False, True),
    ],
)
def test_status_dirty_and_diverged(counts: dict[str, int], dirty: bool, diverged: bool) -> None:
    status = RepoStatus(branch="main", **counts)
    assert (status.dirty, status.diverged) == (dirty, diverged)


def test_sync_outcome_rejects_legacy_ignored_action() -> None:
    with pytest.raises(ValidationError):
        SyncOutcome(workspace="prod", repo="svc-a", target_path=Path("/ws/svc-a"), action="ignored")
