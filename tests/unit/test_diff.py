"""Tests for the unified-diff text primitives."""

from untaped.diff import DiffStats, diff_stats, unified_diff_text


def test_unified_diff_has_patch_compatible_headers() -> None:
    out = unified_diff_text("a\n", "b\n", path="dir/file.txt")
    assert out.startswith("--- a/dir/file.txt\n+++ b/dir/file.txt\n")
    assert "-a" in out and "+b" in out


def test_unified_diff_none_before_means_created_file() -> None:
    out = unified_diff_text(None, "new\n", path="f")
    removed_lines = [
        line for line in out.splitlines() if line.startswith("-") and not line.startswith("---")
    ]
    assert "+new" in out
    assert removed_lines == []


def test_unified_diff_none_after_means_deleted_file() -> None:
    out = unified_diff_text("old\n", None, path="f")
    assert "-old" in out


def test_unified_diff_identical_content_is_empty() -> None:
    assert unified_diff_text("same\n", "same\n", path="f") == ""


def test_diff_stats_counts_added_and_removed_lines() -> None:
    assert diff_stats("a\nb\n", "a\nc\nd\n") == DiffStats(added=2, removed=1)


def test_diff_stats_identical_is_zero() -> None:
    assert diff_stats("x\n", "x\n") == DiffStats(added=0, removed=0)
