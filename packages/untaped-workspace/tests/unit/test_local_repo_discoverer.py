"""Unit tests for LocalRepoDiscoverer (filesystem + injected inspector).

The discoverer is typed against ``GitRunner`` per the package's
"infrastructure depends on subprocess directly" rule, but Python's
duck-typing lets us pass a stub that exposes the same two methods —
which keeps these unit tests free of git subprocess calls.
"""

from pathlib import Path
from typing import cast

from untaped_workspace.infrastructure import LocalRepoDiscoverer
from untaped_workspace.infrastructure.git_runner import GitRunner


class _StubInspector:
    """Map repo path → (url, branch). Missing entries return (None, None)."""

    def __init__(self, table: dict[Path, tuple[str | None, str | None]]) -> None:
        self._table = table

    def read_remote_url(self, repo_path: Path, *, remote: str = "origin") -> str | None:
        return self._table.get(repo_path, (None, None))[0]

    def read_current_branch(self, repo_path: Path) -> str | None:
        return self._table.get(repo_path, (None, None))[1]


def _make(table: dict[Path, tuple[str | None, str | None]]) -> LocalRepoDiscoverer:
    return LocalRepoDiscoverer(cast(GitRunner, _StubInspector(table)))


def _seed_repo(parent: Path, name: str, *, with_git: bool = True) -> Path:
    repo = parent / name
    repo.mkdir()
    if with_git:
        (repo / ".git").mkdir()
    return repo


def test_discover_returns_all_git_subdirs_sorted(tmp_path: Path) -> None:
    b = _seed_repo(tmp_path, "b")
    a = _seed_repo(tmp_path, "a")
    result = _make(
        {
            a: ("https://x/a.git", "main"),
            b: ("https://x/b.git", "develop"),
        }
    ).discover(tmp_path)
    assert [(d.name, d.url, d.branch) for d in result.repos] == [
        ("a", "https://x/a.git", "main"),
        ("b", "https://x/b.git", "develop"),
    ]
    assert result.skipped == []


def test_discover_skips_non_git_directories(tmp_path: Path) -> None:
    _seed_repo(tmp_path, "notes", with_git=False)
    a = _seed_repo(tmp_path, "a")
    result = _make({a: ("https://x/a.git", "main")}).discover(tmp_path)
    assert [d.name for d in result.repos] == ["a"]


def test_discover_skips_files(tmp_path: Path) -> None:
    (tmp_path / "loose.txt").write_text("x")
    a = _seed_repo(tmp_path, "a")
    result = _make({a: ("https://x/a.git", "main")}).discover(tmp_path)
    assert [d.name for d in result.repos] == ["a"]


def test_discover_reports_skipped_when_no_origin(tmp_path: Path) -> None:
    a = _seed_repo(tmp_path, "a")
    b = _seed_repo(tmp_path, "b")
    result = _make(
        {
            a: ("https://x/a.git", "main"),
            b: (None, None),  # no origin
        }
    ).discover(tmp_path)

    assert [d.name for d in result.repos] == ["a"]
    assert len(result.skipped) == 1
    assert "b" in result.skipped[0]
    assert "origin" in result.skipped[0]


def test_discover_records_none_branch_on_detached_head(tmp_path: Path) -> None:
    a = _seed_repo(tmp_path, "a")
    result = _make({a: ("https://x/a.git", None)}).discover(tmp_path)
    assert result.repos[0].branch is None


def test_discover_skips_symlinked_directories(tmp_path: Path) -> None:
    real = _seed_repo(tmp_path, "real")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    result = _make({real: ("https://x/real.git", "main")}).discover(tmp_path)

    assert [d.name for d in result.repos] == ["real"]
    assert any("link" in s and "symlink" in s for s in result.skipped), result.skipped
