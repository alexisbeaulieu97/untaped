"""github's ``setup migrate-dirs`` rows: partial failures, dropped copies, a root that stays."""

from __future__ import annotations

import subprocess
from pathlib import Path

from untaped.sdk import MigrationOptions
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

    rows = preview_cache(MigrationOptions())
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

    (row,) = preview_cache(MigrationOptions())[:1]
    assert "1 repo already in the store" in row.detail
    (outcome,) = apply_cache()

    assert outcome.action == "moved"
    assert "1 copy the store already held dropped" in outcome.detail
    assert not _root().exists()


def test_no_cache_is_unchanged() -> None:
    assert preview_cache(MigrationOptions()) == []
    assert [o.action for o in apply_cache()] == ["unchanged"]


def test_a_root_configured_to_the_store_stays(tmp_path: Path, monkeypatch) -> None:
    from untaped.settings import get_settings

    store = tmp_path / "store"
    repo = store / "git.example" / "app.git"
    _bare("clone", "-q", "--bare", git_remote(tmp_path).url, str(repo))
    monkeypatch.setenv("UNTAPED_GIT__STORE_DIR", str(store))
    monkeypatch.setenv("UNTAPED_GITHUB__CACHE_DIR", str(store))
    get_settings.cache_clear()

    (row,) = [row for row in preview_cache(MigrationOptions()) if row.source == str(store)]
    assert row.action == "keep" and "overlaps the repo store" in row.detail
    apply_cache()
    assert (
        "refs/heads/main"
        in subprocess.run(
            ["git", "--git-dir", str(repo), "for-each-ref"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )


def test_workspaces_repos_in_a_shared_root_are_left_to_its_row(tmp_path: Path) -> None:
    remote = git_remote(tmp_path)
    repo = _root() / "git.example" / "app.git"
    _bare("clone", "-q", "--bare", remote.url, str(repo))
    _bare("--git-dir", str(repo), "config", "untaped.layout", "2")

    assert preview_cache(MigrationOptions()) == []
    (outcome,) = apply_cache()

    assert outcome.action == "moved" and "moved 0 repos" in outcome.detail
    assert repo.is_dir()


def test_a_removal_an_interrupted_run_left_is_finished() -> None:
    leftover = _root() / "git.example" / "app.git.removing"
    (leftover / "objects").mkdir(parents=True)

    (row,) = preview_cache(MigrationOptions())
    assert (row.action, row.source) == ("delete", str(leftover))
    (outcome,) = apply_cache()

    assert outcome.action == "moved"
    assert not _root().exists()
