"""scripts/changelog.py: fragments, derived links, drafts and release builds."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import changelog
from repo.support import REPO_ROOT

PR = "https://github.com/alexisbeaulieu97/untaped/pull"
ISSUE = "https://github.com/alexisbeaulieu97/untaped/issues"
RELEASED = (
    "# Changelog\n\n## 10.0.0\n\n### Added\n\n- old\n\n## Older releases\n\n"
    "Each earlier major has its own file: [9.x](changelog/9.x.md).\n"
)


def _entries(root: Path, **kwargs: object) -> dict[str, list[str]]:
    return changelog.collect(root, **kwargs)  # type: ignore[arg-type]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _write(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path, "README.md", "x\n")
    _commit(tmp_path, "base")
    return tmp_path


def _merge_pr(repo: Path, number: int, files: dict[str, str]) -> None:
    """Land ``files`` on main the way GitHub does, then point origin/main at it."""
    _git(repo, "checkout", "-qb", f"pr{number}")
    for name, text in files.items():
        _write(repo, name, text)
    _commit(repo, f"pr {number}")
    _git(repo, "checkout", "-q", "main")
    _git(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "-m",
        f"Merge pull request #{number} from o/pr{number}",
        f"pr{number}",
    )
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")


# -- fragment files --------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content", "error"),
    [
        ("Bad_Slug.added.md", "x\n", "name it <slug>.<type>.md"),
        ("x.md", "x\n", "name it <slug>.<type>.md"),
        ("x.news.md", "x\n", "type 'news' is not one of upgrading, added"),
        ("x.added.md", "\n", "is empty"),
        ("x.added.md", "- x\n", "starts with a bullet"),
        ("x.added.md", "# Added\n", "starts with a heading"),
        ("x.added.md", "one\n\ntwo\n", "has a blank line"),
        ("x.added.md", " one\n", "starts with whitespace"),
        ("x.added.md", f"one ([#1]({PR}/x))\n", "malformed link group"),
        ("x.added.md", f"one ([#1]({PR}/1) and more)\n", "malformed link group"),
    ],
)
def test_a_malformed_fragment_says_why(name: str, content: str, error: str) -> None:
    fragment, errors = changelog.parse_fragment(name, content)

    assert fragment is None
    assert len(errors) >= 1
    assert error in errors[0]
    assert errors[0].startswith("changelog.d/" + name)


def test_a_fragment_keeps_its_slug_type_and_text() -> None:
    fragment, errors = changelog.parse_fragment("a-b.fixed.md", "Line one\nline two\n")

    assert errors == []
    assert fragment == changelog.Fragment(
        "changelog.d/a-b.fixed.md", "a-b", "fixed", "Line one\nline two"
    )


def test_fragment_type_reads_only_valid_fragment_paths() -> None:
    assert changelog.fragment_type("changelog.d/x.upgrading.md") == "upgrading"
    assert changelog.fragment_type("changelog.d/x.news.md") is None
    assert changelog.fragment_type("docs/x.added.md") is None


def test_breaking_is_the_bold_prefix() -> None:
    assert changelog.is_breaking("**Breaking (awx):** x")
    assert not changelog.is_breaking("awx: **Breaking** x")


def test_missing_and_empty_fragment_directories_are_empty(tmp_path: Path) -> None:
    assert changelog.load_fragments(tmp_path) == []
    (tmp_path / "changelog.d").mkdir()
    assert changelog.load_fragments(tmp_path) == []
    assert changelog.draft_text(_entries(tmp_path)) == ""


def test_check_reports_every_bad_file_and_stray_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "changelog.d/ok.added.md", "fine\n")
    _write(tmp_path, "changelog.d/NOTES.md", "x\n")
    _write(tmp_path, "changelog.d/bad.fixed.md", "- x\n")
    (tmp_path / "changelog.d/sub").mkdir()

    assert changelog.main(["--root", str(tmp_path), "check"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert [line.split(":")[0] for line in err] == [
        "changelog.d/NOTES.md",
        "changelog.d/bad.fixed.md",
        "changelog.d/sub",
    ]


# -- links -----------------------------------------------------------------


def test_a_link_is_derived_from_the_pr_that_added_the_fragment(repo: Path) -> None:
    _merge_pr(repo, 7, {"changelog.d/one.added.md": "First.\n"})
    _merge_pr(repo, 9, {"changelog.d/two.added.md": "Second.\n"})

    assert _entries(repo) == {"added": [f"- First. ([#7]({PR}/7))", f"- Second. ([#9]({PR}/9))"]}


def test_entries_keep_merge_order_not_slug_order(repo: Path) -> None:
    _merge_pr(repo, 7, {"changelog.d/zzz.added.md": "First.\n"})
    _merge_pr(repo, 9, {"changelog.d/aaa.added.md": "Second.\n"})

    assert [e.split(" ")[1] for e in _entries(repo)["added"]] == ["First.", "Second."]


def test_a_link_goes_on_its_own_line_when_it_does_not_fit(repo: Path) -> None:
    text = (
        "A sentence long enough that the link cannot join its last line without\n"
        "wrapping to a longer last line."
    )
    _merge_pr(repo, 7, {"changelog.d/long.added.md": text + "\n"})

    assert _entries(repo) == {
        "added": [f"- {text.replace(chr(10), chr(10) + '  ')}\n  ([#7]({PR}/7))"]
    }


def test_a_fragment_not_on_main_takes_pr_or_a_placeholder(repo: Path) -> None:
    _merge_pr(repo, 7, {"changelog.d/one.added.md": "First.\n"})
    _write(repo, "changelog.d/new.fixed.md", "New.\n")

    assert _entries(repo, pr=12)["fixed"] == [f"- New. ([#12]({PR}/12))"]
    assert _entries(repo)["fixed"] == [f"- New. ([#PR]({PR}/PR))"]
    assert _entries(repo, pr=12)["added"] == [f"- First. ([#7]({PR}/7))"]


def test_a_local_branch_with_main_merged_in_still_derives(repo: Path) -> None:
    _merge_pr(repo, 7, {"changelog.d/one.added.md": "First.\n"})
    _git(repo, "checkout", "-qb", "work")
    _write(repo, "changelog.d/mine.fixed.md", "Mine.\n")
    _commit(repo, "mine")
    _git(repo, "checkout", "-q", "main")
    _merge_pr(repo, 9, {"changelog.d/two.added.md": "Second.\n"})
    _git(repo, "checkout", "-q", "work")
    _git(repo, "merge", "-q", "--no-edit", "main")  # "Merge branch 'main' into work"

    entries = _entries(repo, pr=12)
    assert entries["added"] == [f"- First. ([#7]({PR}/7))", f"- Second. ([#9]({PR}/9))"]
    assert entries["fixed"] == [f"- Mine. ([#12]({PR}/12))"]


def test_a_slug_deleted_and_added_again_takes_the_latest_pr(repo: Path) -> None:
    _merge_pr(repo, 7, {"changelog.d/one.added.md": "First.\n"})
    _git(repo, "rm", "-q", "changelog.d/one.added.md")
    _commit(repo, "built")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _merge_pr(repo, 9, {"changelog.d/one.added.md": "Again.\n"})

    assert _entries(repo) == {"added": [f"- Again. ([#9]({PR}/9))"]}


def test_a_slug_deleted_by_a_release_is_not_on_main_until_added_again(repo: Path) -> None:
    _merge_pr(repo, 7, {"changelog.d/one.added.md": "First.\n"})
    _git(repo, "rm", "-q", "changelog.d/one.added.md")
    _commit(repo, "built")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(repo, "changelog.d/one.added.md", "Again.\n")

    assert _entries(repo, pr=12) == {"added": [f"- Again. ([#12]({PR}/12))"]}


def test_a_fragment_added_outside_a_pr_merge_fails_loudly(repo: Path) -> None:
    _write(repo, "changelog.d/direct.added.md", "Pushed.\n")
    _commit(repo, "oops, pushed to main")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    with pytest.raises(
        changelog.ChangelogError, match=r"direct\.added\.md: added by .*not a 'Merge pull request'"
    ):
        _entries(repo)


def test_without_origin_main_nothing_is_on_main(repo: Path) -> None:
    _write(repo, "changelog.d/new.fixed.md", "New.\n")

    assert _entries(repo, pr=3) == {"fixed": [f"- New. ([#3]({PR}/3))"]}


@pytest.mark.parametrize(
    "text",
    [
        f"Fixes it.\n([#442]({PR}/442))",
        f"Fixes it.\n([#442]({PR}/442),\n[#503]({ISSUE}/503))",
        f"Fixes it ([#4]({PR}/4), [#5]({PR}/5))",
    ],
)
def test_an_entry_with_its_own_link_group_keeps_it_and_gets_no_other(repo: Path, text: str) -> None:
    _merge_pr(repo, 7, {"changelog.d/one.added.md": text + "\n"})

    (entry,) = _entries(repo)["added"]
    assert entry.count("[#") == text.count("[#")
    assert "/pull/7" not in entry
    assert entry.startswith("- Fixes it")


def test_a_wrapped_link_group_is_indented_by_the_renderer(repo: Path) -> None:
    _merge_pr(repo, 7, {"changelog.d/one.added.md": f"Fixes.\n([#4]({PR}/4),\n[#5]({ISSUE}/5))\n"})

    assert _entries(repo)["added"] == [f"- Fixes.\n  ([#4]({PR}/4),\n  [#5]({ISSUE}/5))"]


# -- draft -----------------------------------------------------------------


def test_the_draft_orders_sections_and_renders_only_those_with_fragments(repo: Path) -> None:
    _merge_pr(
        repo,
        7,
        {
            "changelog.d/a.fixed.md": "Fix.\n",
            "changelog.d/b.upgrading.md": "Do this.\n",
            "changelog.d/c.added.md": "Add.\n",
        },
    )

    assert changelog.draft_text(_entries(repo)) == (
        "## Unreleased\n\n"
        f"### Upgrading\n\n- Do this. ([#7]({PR}/7))\n\n"
        f"### Added\n\n- Add. ([#7]({PR}/7))\n\n"
        f"### Fixed\n\n- Fix. ([#7]({PR}/7))\n"
    )


def test_draft_prints_the_section_and_nothing_without_fragments(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert changelog.main(["--root", str(repo), "draft"]) == 0
    assert capsys.readouterr().out == ""

    _write(repo, "changelog.d/a.fixed.md", "Fix.\n")
    assert changelog.main(["--root", str(repo), "draft", "--pr", "5"]) == 0
    assert capsys.readouterr().out == f"## Unreleased\n\n### Fixed\n\n- Fix. ([#5]({PR}/5))\n"


# -- build -----------------------------------------------------------------


def _build(text: str, version: str, **entries: list[str]) -> str:
    return changelog.build_text(text, version, entries)[0]


def test_a_minor_build_adds_the_section_on_top() -> None:
    built = _build(RELEASED, "10.1.0", added=["- new"], fixed=["- bug"])

    assert built.startswith(
        "# Changelog\n\n## 10.1.0\n\n### Added\n\n- new\n\n### Fixed\n\n- bug\n\n## 10.0.0\n"
    )
    assert built.endswith("[9.x](changelog/9.x.md).\n")


def test_an_empty_build_is_refused() -> None:
    with pytest.raises(changelog.ChangelogError, match="nothing to release"):
        _build(RELEASED, "10.1.0")


def test_a_second_build_of_a_version_is_refused() -> None:
    with pytest.raises(changelog.ChangelogError, match=r'already has a "## 10\.0\.0" section'):
        _build(RELEASED, "10.0.0", added=["- x"])


@pytest.mark.parametrize("version", ["10.1.0", "10.0.1", "10.1.0rc1"])
def test_an_upgrading_fragment_is_refused_outside_a_major(version: str) -> None:
    with pytest.raises(changelog.ChangelogError, match="upgrading fragments"):
        _build(RELEASED, version, upgrading=["- do"])


def test_a_bad_version_is_refused() -> None:
    with pytest.raises(changelog.ChangelogError, match=r"is not X\.Y\.Z"):
        _build(RELEASED, "v11", added=["- x"])


def test_a_pre_release_rolls_into_the_final_build() -> None:
    rc1 = _build(RELEASED, "11.0.0rc1", upgrading=["- do"], added=["- a"])
    rc2 = _build(rc1, "11.0.0rc2", added=["- b"], fixed=["- f"])
    final = _build(rc2, "11.0.0")  # no new fragments: still a build

    assert "## 11.0.0rc1" in rc1
    assert "rc2" not in final
    section = final.split("## 11.0.0\n", 1)[1].split("\n## ", 1)[0]
    assert section == "\n### Upgrading\n\n- do\n\n### Added\n\n- a\n- b\n\n### Fixed\n\n- f\n"
    assert final.count("## 11.0.0") == 1


def test_a_hand_written_opening_paragraph_survives_a_later_build() -> None:
    first = _build(RELEASED, "11.0.0rc1", upgrading=["- do"])
    intro = first.replace("## 11.0.0rc1\n", "## 11.0.0rc1\n\nA big one.\n")

    assert "## 11.0.0\n\nA big one.\n\n### Upgrading\n" in _build(intro, "11.0.0", added=["- a"])


def test_a_major_build_archives_the_previous_major() -> None:
    built, archives = changelog.build_text(RELEASED, "11.0.0", {"upgrading": ["- do"]})

    assert archives == {
        10: "# Changelog: untaped 10.x\n\n"
        "Newer releases are in [CHANGELOG.md](../CHANGELOG.md).\n\n"
        "## 10.0.0\n\n### Added\n\n- old\n"
    }
    assert built == (
        "# Changelog\n\n## 11.0.0\n\n### Upgrading\n\n- do\n\n## Older releases\n\n"
        "Each earlier major has its own file: [10.x](changelog/10.x.md) and\n"
        "[9.x](changelog/9.x.md).\n"
    )
    # The final build after an rc finds nothing left to archive.
    assert (
        changelog.build_text(built.replace("## 11.0.0\n", "## 11.0.0rc1\n"), "11.0.0", {})[1] == {}
    )


def test_the_older_releases_line_wraps_like_the_file_does() -> None:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    body = text.split("## Older releases\n\n", 1)[1].strip()

    assert changelog.older_releases([9, 8, 7, 6, 5, 4]) == body


def test_the_real_changelog_survives_a_major_build() -> None:
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    built, archives = changelog.build_text(text, "11.0.0", {"upgrading": ["- do"]})

    assert list(archives) == [10]
    assert archives[10].count("\n## ") == 1  # 10.0.0
    assert "## 10.0.0" not in built
    assert "file: [10.x](changelog/10.x.md),\n[9.x](changelog/9.x.md)," in built


def test_build_writes_the_files_and_removes_the_fragments(repo: Path) -> None:
    _write(repo, "CHANGELOG.md", RELEASED)
    _merge_pr(repo, 7, {"changelog.d/a.added.md": "Add.\n", "changelog.d/b.upgrading.md": "Do.\n"})

    assert changelog.main(["--root", str(repo), "build", "11.0.0"]) == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"### Upgrading\n\n- Do. ([#7]({PR}/7))\n\n### Added\n\n- Add. ([#7]({PR}/7))\n" in text
    assert (
        (repo / "changelog/10.x.md")
        .read_text(encoding="utf-8")
        .startswith("# Changelog: untaped 10.x")
    )
    assert not (repo / "changelog.d").exists()


def test_build_refuses_to_overwrite_an_archive(repo: Path) -> None:
    _write(repo, "CHANGELOG.md", RELEASED)
    _write(repo, "changelog/10.x.md", "old\n")
    _merge_pr(repo, 7, {"changelog.d/a.upgrading.md": "Do.\n"})

    with pytest.raises(changelog.ChangelogError, match=r"changelog/10\.x\.md already exists"):
        changelog.build(repo, "11.0.0")
    assert (repo / "changelog.d/a.upgrading.md").exists()


def test_build_refuses_a_fragment_that_is_not_on_main(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repo, "CHANGELOG.md", RELEASED)
    _write(repo, "changelog.d/a.added.md", "Add.\n")

    assert changelog.main(["--root", str(repo), "build", "10.1.0"]) == 1
    assert "a.added.md: not on origin/main" in capsys.readouterr().err
    assert (repo / "changelog.d/a.added.md").exists()
