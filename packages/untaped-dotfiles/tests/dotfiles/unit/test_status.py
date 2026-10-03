"""The state of a placed path, and the policy table (one case per cell)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from untaped_dotfiles.domain.models import AppliedRecord, Policy
from untaped_dotfiles.domain.status import (
    FileState,
    SourceInfo,
    TargetInfo,
    file_state,
    needs_attention,
    sync_action,
)

T0 = datetime(2026, 10, 2, tzinfo=UTC)


def _record(mode: str, source_hash: str = "s1", target_hash: str = "t1") -> AppliedRecord:
    return AppliedRecord(
        target="/home/x/a",
        repo="r",
        item="i",
        file="a",
        source="a",
        mode=mode,  # type: ignore[arg-type]
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


def test_a_source_absent_from_the_repo_is_orphan() -> None:
    assert file_state("copy", None, SourceInfo(False), TargetInfo("missing")) == (
        "orphan",
        "the source is not in the repo",
    )
    assert file_state(
        "copy", _record("copy"), SourceInfo(False), TargetInfo("file", hash="t1")
    ) == (
        "orphan",
        "the source is gone from the repo",
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


# -- the policy table ---------------------------------------------------------------

TABLE: dict[tuple[Policy, FileState], str] = {
    ("sync", "pending"): "apply",
    ("sync", "foreign"): "apply",
    ("sync", "applied"): "skip",
    ("sync", "behind"): "apply",
    ("sync", "modified"): "report",
    ("sync", "conflict"): "report",
    ("sync", "missing"): "apply",
    ("sync", "orphan"): "remove",
    ("once", "pending"): "apply",
    ("once", "foreign"): "apply",
    ("once", "applied"): "skip",
    ("once", "behind"): "skip",
    ("once", "modified"): "skip",
    ("once", "conflict"): "skip",
    ("once", "missing"): "report",
    ("once", "orphan"): "report",
    ("manual", "pending"): "report",
    ("manual", "foreign"): "report",
    ("manual", "applied"): "skip",
    ("manual", "behind"): "report",
    ("manual", "modified"): "report",
    ("manual", "conflict"): "report",
    ("manual", "missing"): "report",
    ("manual", "orphan"): "report",
}


@pytest.mark.parametrize(("policy", "state"), sorted(TABLE), ids=lambda v: str(v))
def test_sync_action_table(policy: Policy, state: FileState) -> None:
    assert sync_action(policy, state) == TABLE[(policy, state)]


def test_attention_counts_what_needs_the_user() -> None:
    assert needs_attention("manual", "behind")
    assert needs_attention("sync", "modified")
    assert needs_attention("sync", "conflict")
    assert not needs_attention("sync", "applied")
    assert not needs_attention("sync", "pending")
    assert not needs_attention("once", "behind")
    assert needs_attention("once", "missing")
    assert not needs_attention("manual", "excluded")
