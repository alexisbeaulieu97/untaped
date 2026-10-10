"""Reading a store repo's own files without running git."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from untaped_git.infrastructure.repo_files import config_value, list_repos, repo_origin


def test_list_repos_finds_leaves_and_skips_the_rest(tmp_path: Path) -> None:
    for rel in (
        "h/acme/app.git/objects",
        "h/grp/sub/lib.git",
        "h/acme/.github.git",
        "_unknown/abc.git",
        ".validate-x/y.git",
        "h/acme/notes",
    ):
        (tmp_path / rel).mkdir(parents=True)
    (tmp_path / "h" / "acme" / "app.git.lock").write_text("")
    (tmp_path / "h" / "link.git").symlink_to(tmp_path / "h" / "acme" / "app.git")

    assert list_repos(tmp_path) == [
        tmp_path / "_unknown" / "abc.git",
        tmp_path / "h" / "acme" / ".github.git",
        tmp_path / "h" / "acme" / "app.git",
        tmp_path / "h" / "grp" / "sub" / "lib.git",
    ]
    assert list_repos(tmp_path / "missing") == []


@pytest.mark.parametrize(
    "values",
    [
        ["https://github.com/acme/app.git"],
        ["https://h/a#b;c.git"],
        ['https://h/"quoted".git'],
        ["C:\\repos\\app.git"],
        ["  padded\ttab  "],
        ["https://first/a.git", "https://last/b.git"],
    ],
    ids=["plain", "comment-chars", "quotes", "backslashes", "whitespace", "last-wins"],
)
def test_repo_origin_reads_what_git_config_reads(tmp_path: Path, values: list[str]) -> None:
    config = _config_file(tmp_path)
    for value in values:
        _git("config", "--file", str(config), "--add", "remote.origin.url", value)

    assert repo_origin(config.parent) == _git_get(config, "remote.origin.url") == values[-1]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("url = ab ; comment", "ab"),
        ('url = "" x', "x"),
        ('url=  "  q  "  r  # c', "  q    r"),
        ("URL = a\\tb", "a\tb"),
        ("url = a\u00a0b\u00a0", "a\u00a0b\u00a0"),
        ("url = a\vb\v", "a\vb\v"),
    ],
    ids=["comment", "empty-quotes", "quoted-padding", "escape", "nbsp", "vt"],
)
def test_repo_origin_parses_hand_written_values_like_git(
    tmp_path: Path, line: str, expected: str
) -> None:
    config = _config_file(tmp_path)
    config.write_bytes(f'[remote "origin"] # a comment\n\t{line}\n'.encode())

    assert repo_origin(config.parent) == _git_get(config, "remote.origin.url") == expected


def test_repo_origin_is_none_without_an_origin_or_a_config(tmp_path: Path) -> None:
    config = _config_file(tmp_path)
    config.write_text(
        '[remote "upstream"]\n\turl = https://h/a.git\n[remote "Origin"]\n\turl = x\n'
    )

    assert repo_origin(config.parent) is None
    assert repo_origin(tmp_path / "missing.git") is None


def test_a_plain_section_matches_any_case_and_the_last_value_wins(tmp_path: Path) -> None:
    config = _config_file(tmp_path)
    config.write_text(
        '[untaped]\n\towner = a\n[untaped "sub"]\n\towner = b\n[UNTAPED] ; c\n\tOwner = "c" # n\n'
    )

    assert config_value(config, "untaped", "owner") == _git_get(config, "untaped.owner") == "c"
    assert config_value(config, "untaped", "missing") is None


def _config_file(tmp_path: Path) -> Path:
    """``<repo>/config`` with no repository around it: the readers read only the file."""
    (tmp_path / "app.git").mkdir()
    return tmp_path / "app.git" / "config"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], text=True, capture_output=True, check=True
    ).stdout.removesuffix("\n")


def _git_get(config: Path, key: str) -> str:
    """``key`` as ``git config --get`` reads ``config``."""
    return _git("config", "--file", str(config), "--get", key)
