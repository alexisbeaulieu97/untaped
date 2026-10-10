"""The `git.store` report reads the store from disk alone."""

from __future__ import annotations

from pathlib import Path

from untaped_git.cli.store_view import report_lines
from untaped_git.infrastructure.report import store_report


def _repo(root: Path, *parts: str, config: str = "", packs: int = 0, loose: int = 0) -> Path:
    repo = root.joinpath(*parts)
    (repo / "objects" / "pack").mkdir(parents=True)
    for index in range(packs):
        (repo / "objects" / "pack" / f"pack-{index}.pack").write_bytes(b"x" * 10)
        (repo / "objects" / "pack" / f"pack-{index}.idx").write_bytes(b"")
    if loose:
        (repo / "objects" / "ab").mkdir()
        for index in range(loose):
            (repo / "objects" / "ab" / f"{index:038d}").write_bytes(b"")
    (repo / "objects" / "info").mkdir()
    (repo / "config").write_text(config, encoding="utf-8")
    return repo


def test_a_store_report(tmp_path: Path) -> None:
    root = tmp_path / "store"
    ignored = "[untaped]\n\tfilter = honoured\n[Untaped]\n\tfilter = ignored\n"
    _repo(root, "github.com", "acme", "a.git", config=ignored, packs=3, loose=2)
    gone = _repo(root, "github.com", "acme", "b.git", packs=1)
    (gone / "gc.log").write_text("error\n", encoding="utf-8")
    _repo(root, "git.example", "c.git", config="[untaped]\n\tfilter = honoured\n")

    report = store_report(root, version="2.43.0")

    assert (report.repos, report.git_version) == (3, "2.43.0")
    assert (report.packs_median, report.packs_max, report.loose_objects) == (1, 3, 2)
    assert report.filter_ignored == {"github.com": 1}
    assert report.gc_log == ["github.com/acme/b.git"]
    assert report.size_bytes >= 40


def test_an_empty_or_missing_store(tmp_path: Path) -> None:
    report = store_report(tmp_path / "missing", version=None)
    assert (report.repos, report.size_bytes, report.packs_median, report.packs_max) == (0, 0, 0, 0)


def _worktree(repo: Path, name: str, owner: str | None) -> None:
    admin = repo / "worktrees" / name
    admin.mkdir(parents=True)
    (admin / "gitdir").write_text(f"/elsewhere/{name}/.git\n", encoding="utf-8")
    if owner is not None:
        stamp = f"[untaped]\n\towner = {owner}\n\tworktree = /elsewhere/{name}\n"
        (admin / "config.worktree").write_text(stamp, encoding="utf-8")


def test_who_uses_each_repo_and_what_only_one_plugin_uses(tmp_path: Path) -> None:
    root = tmp_path / "store"
    shared = _repo(root, "github.com", "acme", "shared.git", packs=2)
    (shared / "untaped-github.json").write_text("{}")
    _worktree(shared, "one", "workspace")
    _worktree(shared, "two", "workspace")
    _worktree(shared, "hand", None)
    only = _repo(root, "github.com", "acme", "only.git", packs=1)
    (only / "untaped-github.json").write_text("{}")
    _repo(root, "github.com", "acme", "marked.git", config="[untaped]\n\trelease = github\n")
    _repo(root, "github.com", "acme", "branches.git", packs=1)

    report = store_report(root, version=None)

    assert report.used_by == {"github": 2, "workspace": 1}
    assert report.exclusive_bytes == {"github": report.exclusive_bytes["github"]}
    assert report.exclusive_bytes["github"] >= 10
    assert (report.unowned.repos, report.held_by_branches.repos) == (1, 1)
    lines = report_lines(report)
    assert [line.split("  ")[0] for line in lines] == [
        "store",
        "used by",
        "exclusive",
        "unowned",
        "held by branches",
        "packs",
    ]
    assert lines[1].endswith("github 2 · workspace 1")
    assert "1 repo," in lines[3] and "interrupted release" in lines[3]


def test_an_empty_store_prints_one_line(tmp_path: Path) -> None:
    (line,) = report_lines(store_report(tmp_path / "missing", version="2.54.0"))
    assert line.endswith("0 repos    0 B    git 2.54.0")
