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
    assert out.startswith("--- /dev/null\n+++ b/f\n")
    assert "+new" in out
    assert removed_lines == []


def test_unified_diff_none_after_means_deleted_file() -> None:
    out = unified_diff_text("old\n", None, path="f")
    assert out.startswith("--- a/f\n+++ /dev/null\n")
    assert "-old" in out


def test_unified_diff_missing_final_newline_does_not_merge_lines() -> None:
    out = unified_diff_text("a\nb", "a\nc", path="f")
    lines = out.splitlines()
    assert "-b" in lines
    assert "+c" in lines
    assert lines.count(r"\ No newline at end of file") == 2


def test_unified_diff_created_file_missing_final_newline_marks_it() -> None:
    out = unified_diff_text(None, "new", path="f")
    assert out.startswith("--- /dev/null\n+++ b/f\n")
    assert "+new\n\\ No newline at end of file\n" in out


def test_unified_diff_deleted_file_missing_final_newline_marks_it() -> None:
    out = unified_diff_text("old", None, path="f")
    assert out.startswith("--- a/f\n+++ /dev/null\n")
    assert "-old\n\\ No newline at end of file\n" in out


def test_unified_diff_identical_content_is_empty() -> None:
    assert unified_diff_text("same\n", "same\n", path="f") == ""


def test_diff_stats_counts_added_and_removed_lines() -> None:
    assert diff_stats("a\nb\n", "a\nc\nd\n") == DiffStats(added=2, removed=1)


def test_diff_stats_counts_lines_that_look_like_file_headers() -> None:
    assert diff_stats("-- removed\nkeep\n", "keep\n") == DiffStats(added=0, removed=1)
    assert diff_stats("keep\n", "++ added\nkeep\n") == DiffStats(added=1, removed=0)


def test_diff_stats_identical_is_zero() -> None:
    assert diff_stats("x\n", "x\n") == DiffStats(added=0, removed=0)
