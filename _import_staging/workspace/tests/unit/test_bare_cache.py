from pathlib import Path

from untaped_workspace.infrastructure.bare_cache import cache_path_for


def test_https_url_path(tmp_path: Path) -> None:
    p = cache_path_for("https://github.com/org/svc-a.git", cache_dir=tmp_path)
    assert p == (tmp_path / "github.com/org/svc-a.git").resolve()


def test_ssh_url_path(tmp_path: Path) -> None:
    p = cache_path_for("git@github.com:org/svc-bee.git", cache_dir=tmp_path)
    assert p == (tmp_path / "github.com/org/svc-bee.git").resolve()


def test_url_without_dot_git_suffix(tmp_path: Path) -> None:
    p = cache_path_for("https://github.com/org/svc-c", cache_dir=tmp_path)
    assert p == (tmp_path / "github.com/org/svc-c.git").resolve()


def test_unparseable_url_falls_back_to_hashed_leaf(tmp_path: Path) -> None:
    p = cache_path_for("/local/path/no-host", cache_dir=tmp_path)
    # Falls into _unknown
    assert p.parent == (tmp_path / "_unknown").resolve()
    assert p.suffix == ".git"


def test_file_url(tmp_path: Path) -> None:
    p = cache_path_for("file:///tmp/foo/svc-a.git", cache_dir=tmp_path)
    # urlparse gives empty host for file://; falls back to _unknown
    assert "_unknown" in p.parts or "svc-a.git" in p.name
