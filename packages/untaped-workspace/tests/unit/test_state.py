import pytest
from pydantic import ValidationError
from untaped_workspace.domain import RepoStatus, SyncOutcome


def test_dirty_when_modified_or_untracked() -> None:
    assert RepoStatus(branch="main", modified=1).dirty
    assert RepoStatus(branch="main", untracked=1).dirty
    assert not RepoStatus(branch="main").dirty


def test_diverged_when_both_ahead_and_behind() -> None:
    assert RepoStatus(branch="main", ahead=1, behind=1).diverged
    assert not RepoStatus(branch="main", ahead=1, behind=0).diverged
    assert not RepoStatus(branch="main", ahead=0, behind=1).diverged


def test_status_is_frozen() -> None:
    s = RepoStatus(branch="main")
    with pytest.raises(ValidationError):
        s.modified = 99  # type: ignore[misc]


def test_sync_outcome_default_detail() -> None:
    o = SyncOutcome(workspace="prod", repo="svc-a", action="clone")
    assert o.detail == ""
