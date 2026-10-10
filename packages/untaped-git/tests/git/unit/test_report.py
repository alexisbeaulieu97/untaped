"""The `git.store` report reads the store from disk alone."""

from __future__ import annotations

from pathlib import Path

from untaped_git.application.report import store_report


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
