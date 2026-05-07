"""Unit tests for LocalRepoDiscoverer (filesystem + injected inspector)."""

from pathlib import Path

from untaped_workspace.infrastructure import LocalRepoDiscoverer


class _StubInspector:
    """Map repo path → (url, branch). Missing entries return (None, None)."""

    def __init__(self, table: dict[Path, tuple[str | None, str | None]]) -> None:
        self._table = table

    def read_remote_url(self, repo_path: Path, *, remote: str = "origin") -> str | None:
        return self._table.get(repo_path, (None, None))[0]

    def read_current_branch(self, repo_path: Path) -> str | None:
        return self._table.get(repo_path, (None, None))[1]


def _seed_repo(parent: Path, name: str, *, with_git: bool = True) -> Path:
    repo = parent / name
    repo.mkdir()
    if with_git:
        (repo / ".git").mkdir()
    return repo


def test_discover_returns_all_git_subdirs_sorted(tmp_path: Path) -> None:
    b = _seed_repo(tmp_path, "b")
    a = _seed_repo(tmp_path, "a")
    inspector = _StubInspector(
        {
            a: ("https://x/a.git", "main"),
            b: ("https://x/b.git", "develop"),
        }
    )
    result = LocalRepoDiscoverer(inspector).discover(tmp_path)
    assert [(d.name, d.url, d.branch) for d in result] == [
        ("a", "https://x/a.git", "main"),
        ("b", "https://x/b.git", "develop"),
    ]


def test_discover_skips_non_git_directories(tmp_path: Path) -> None:
    _seed_repo(tmp_path, "notes", with_git=False)
    a = _seed_repo(tmp_path, "a")
    inspector = _StubInspector({a: ("https://x/a.git", "main")})
    result = LocalRepoDiscoverer(inspector).discover(tmp_path)
    assert [d.name for d in result] == ["a"]


def test_discover_skips_files(tmp_path: Path) -> None:
    (tmp_path / "loose.txt").write_text("x")
    a = _seed_repo(tmp_path, "a")
    inspector = _StubInspector({a: ("https://x/a.git", "main")})
    result = LocalRepoDiscoverer(inspector).discover(tmp_path)
    assert [d.name for d in result] == ["a"]


def test_discover_warns_and_omits_when_no_origin(tmp_path: Path) -> None:
    a = _seed_repo(tmp_path, "a")
    b = _seed_repo(tmp_path, "b")
    inspector = _StubInspector(
        {
            a: ("https://x/a.git", "main"),
            b: (None, None),  # no origin
        }
    )
    warnings: list[str] = []
    result = LocalRepoDiscoverer(inspector, warn=warnings.append).discover(tmp_path)

    assert [d.name for d in result] == ["a"]
    assert len(warnings) == 1
    assert "b" in warnings[0]
    assert "origin" in warnings[0]


def test_discover_records_none_branch_on_detached_head(tmp_path: Path) -> None:
    a = _seed_repo(tmp_path, "a")
    inspector = _StubInspector({a: ("https://x/a.git", None)})
    result = LocalRepoDiscoverer(inspector).discover(tmp_path)
    assert result[0].branch is None
