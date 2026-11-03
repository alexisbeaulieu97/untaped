"""The state of a placed path, how a file is placed, and what apply does in each state."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from untaped_dotfiles.models import PlacedRecord
from untaped_dotfiles.policy import Policy
from untaped_dotfiles.states import (
    SourceInfo,
    TargetInfo,
    decide,
    decide_asked,
    file_state,
    placed_as,
)

T0 = datetime(2026, 10, 2, tzinfo=UTC)


def _record(mode: str, source_hash: str = "s1", target_hash: str = "t1") -> PlacedRecord:
    return PlacedRecord(
        target="/home/x/a",
        source="s",
        package="p",
        item="i",
        path="a",
        entry="a",
        placed_as=mode,  # type: ignore[arg-type]
        source_hash=source_hash,
        target_hash=target_hash,
        applied_at=T0,
    )


# -- never applied -----------------------------------------------------------


def test_missing_target_is_pending() -> None:
    assert file_state("copy", None, SourceInfo(True, "s1"), TargetInfo("missing")) == (
        "pending",
        "",
    )


def test_a_file_in_the_way_is_foreign() -> None:
    state, detail = file_state("copy", None, SourceInfo(True, "s1"), TargetInfo("file", hash="x"))
    assert state == "foreign"
    assert "keeps it aside" in detail


def test_an_identical_file_or_the_right_link_is_pending_not_foreign() -> None:
    same = TargetInfo("file", hash="s1")
    assert file_state("copy", None, SourceInfo(True, "s1"), same)[0] == "pending"
    linked = TargetInfo("symlink", link_to="/repo/a")
    assert file_state("link", None, SourceInfo(True, "/repo/a"), linked) == (
        "pending",
        "already linked",
    )
    assert (
        file_state("link", None, SourceInfo(True, "/repo/a"), TargetInfo("file", hash="x"))[0]
        == "foreign"
    )


def test_merge_into_an_existing_file_is_pending() -> None:
    state, _ = file_state("merge", None, SourceInfo(True, "s1"), TargetInfo("file", hash="x"))
    assert state == "pending"


def test_a_path_absent_from_the_source_is_orphan() -> None:
    assert file_state("copy", None, SourceInfo(False), TargetInfo("missing")) == (
        "orphan",
        "the source doesn't have it",
    )
    assert file_state(
        "copy", _record("copy"), SourceInfo(False), TargetInfo("file", hash="t1")
    ) == (
        "orphan",
        "the source no longer has it",
    )


# -- applied before ------------------------------------------------------------


def test_applied_behind_modified_conflict_missing_for_copy() -> None:
    record = _record("copy")
    ours = TargetInfo("file", hash="t1")
    edited = TargetInfo("file", hash="t2")
    assert file_state("copy", record, SourceInfo(True, "s1"), ours)[0] == "applied"
    assert file_state("copy", record, SourceInfo(True, "s2"), ours)[0] == "behind"
    assert file_state("copy", record, SourceInfo(True, "s1"), edited)[0] == "modified"
    assert file_state("copy", record, SourceInfo(True, "s2"), edited)[0] == "conflict"
    assert file_state("copy", record, SourceInfo(True, "s1"), TargetInfo("missing"))[0] == "missing"
    assert file_state("copy", record, SourceInfo(True, "s1"), TargetInfo("dir")) == (
        "modified",
        "the target is now a dir",
    )


def test_link_is_behind_only_when_the_fetched_ref_changed_it() -> None:
    record = _record("link", source_hash="/repo/a", target_hash="/repo/a")
    linked = TargetInfo("symlink", link_to="/repo/a")
    assert file_state("link", record, SourceInfo(True, "/repo/a"), linked)[0] == "applied"
    assert file_state("link", record, SourceInfo(True, "/repo/a", changed=True), linked)[0] == (
        "behind"
    )
    replaced = TargetInfo("file", hash="x")
    assert file_state("link", record, SourceInfo(True, "/repo/a"), replaced) == (
        "modified",
        "the link was replaced",
    )
    elsewhere = TargetInfo("symlink", link_to="/other")
    assert file_state("link", record, SourceInfo(True, "/repo/a"), elsewhere) == (
        "modified",
        "the link points elsewhere",
    )
    assert file_state("link", record, SourceInfo(True, "/repo/a", changed=True), replaced)[0] == (
        "conflict"
    )


def test_merge_modified_means_the_managed_keys_changed() -> None:
    record = _record("merge")
    assert file_state("merge", record, SourceInfo(True, "s1"), TargetInfo("file", hash="t2")) == (
        "modified",
        "managed keys edited on this machine",
    )


# -- the policy table (design 7.2) -----------------------------------------------

#: Each state's action under sync, once and manual, as design 7.2 has it.
_TABLE = {
    "pending": ("apply", "apply", "apply"),
    "foreign": ("apply", "apply", "apply"),
    "new": ("apply", "apply", "report"),
    "applied": ("skip", "skip", "skip"),
    "behind": ("apply", "skip", "report"),
    "modified": ("report", "skip", "report"),
    "conflict": ("report", "skip", "report"),
    "missing": ("apply", "report", "report"),
    "orphan": ("remove", "report", "report"),
    "off": ("report", "report", "report"),
    "excluded": ("skip", "skip", "skip"),
}


@pytest.mark.parametrize(
    ("state", "policy", "action"),
    [
        (state, policy, actions[column])
        for state, actions in _TABLE.items()
        for column, policy in enumerate(("sync", "once", "manual"))
    ],
)
def test_each_cell_of_the_policy_table(state: str, policy: str, action: str) -> None:
    assert decide(Policy(policy), state, moved=False) == action  # type: ignore[arg-type]


@pytest.mark.parametrize("policy", list(Policy))
def test_an_orphan_an_input_moved_goes_whatever_the_policy(policy: Policy) -> None:
    assert decide(policy, "orphan", moved=True) == "remove"


@pytest.mark.parametrize(
    ("policy", "mode", "placed"),
    [
        ("sync", "link", "link"),
        ("once", "link", "copy"),
        ("manual", "link", "copy"),
        ("sync", "copy", "copy"),
        ("once", "copy", "copy"),
        ("manual", "copy", "copy"),
        ("sync", "merge", "merge"),
        ("once", "merge", "merge"),
        ("manual", "merge", "merge"),
    ],
)
def test_a_link_is_a_link_only_under_sync(policy: str, mode: str, placed: str) -> None:
    assert placed_as(Policy(policy), mode) == placed  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("state", "force", "action"),
    [
        ("pending", False, "apply"),
        ("new", False, "apply"),
        ("behind", False, "apply"),
        ("missing", False, "apply"),
        ("orphan", False, "remove"),
        ("modified", False, None),
        ("conflict", False, None),
        ("modified", True, "apply"),
        ("off", False, "report"),
        ("applied", False, "skip"),
    ],
)
def test_naming_a_package_applies_it_whatever_its_policy(
    state: str, force: bool, action: str | None
) -> None:
    assert decide_asked(state, force=force) == action  # type: ignore[arg-type]
