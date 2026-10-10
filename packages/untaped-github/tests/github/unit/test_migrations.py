"""github's ``setup migrate-dirs`` rows: partial failures, dropped copies, a root that stays."""

from __future__ import annotations

import subprocess
from pathlib import Path

from untaped.testing.git import git_remote
from untaped_github.infrastructure.migrations import apply_cache, preview_cache


def _root() -> Path:
    return Path.home() / ".untaped" / "github-cache"


def _bare(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def test_one_repo_failing_leaves_the_others_moved_and_the_root_kept(tmp_path: Path) -> None:
    remote = git_remote(tmp_path)
    good = _root() / "git.example" / "app.git"
    _bare("clone", "-q", "--bare", "--depth=1", remote.url, str(good))
    orphan = _root() / "git.example" / "orphan.git"
    _bare("init", "-q", "--bare", str(orphan))
    (_root() / "notes.txt").write_text("mine", encoding="utf-8")

    rows = preview_cache()
    assert [row.action for row in rows] == ["move", "then"]
    assert "1 shallow repo" in rows[1].detail

    (outcome,) = apply_cache()

    assert outcome.action == "partial"
    assert "moved 1 repo into the repo store" in outcome.detail
    assert "has no origin URL" in outcome.detail
    assert "something other than repositories is left there" in outcome.detail
    assert not good.exists() and orphan.is_dir()


def test_a_copy_the_store_already_holds_is_dropped(tmp_path: Path) -> None:
    remote = git_remote(tmp_path)
    first = _root() / "git.example" / "app.git"
    _bare("clone", "-q", "--bare", remote.url, str(first))
    apply_cache()
    again = _root() / "git.example" / "app.git"
    _bare("clone", "-q", "--bare", "--depth=1", remote.url, str(again))

    (row,) = preview_cache()[:1]
    assert "1 repo already in the store" in row.detail
    (outcome,) = apply_cache()

    assert outcome.action == "moved"
    assert "1 copy the store already held dropped" in outcome.detail
    assert not _root().exists()


def test_no_cache_is_unchanged() -> None:
    assert preview_cache() == []
    assert [o.action for o in apply_cache()] == ["unchanged"]
