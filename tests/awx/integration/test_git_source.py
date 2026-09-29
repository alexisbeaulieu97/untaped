"""``GitSource``: files as they are at one commit, never the working tree."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from untaped.capabilities.awx.infrastructure.git_source import GitSource
from untaped.capability_api import ConfigError


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A pushed ``work`` branch holding specs, tagged ``v1``, with local edits after it."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(bare)], check=True)
    repo = tmp_path / "clone"
    subprocess.run(["git", "clone", str(bare), str(repo)], check=True, capture_output=True)
    for key, value in [("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")]:
        _git(repo, "config", key, value)
    _git(repo, "checkout", "-b", "work")
    specs = repo / ".untaped" / "awx"
    (specs / "templates" / "nested").mkdir(parents=True)
    (specs / "templates" / "deploy.yml").write_text("v: 1\n")
    (specs / "templates" / "nested" / "b.yaml").write_text("v: b\n")
    (specs / "templates" / "notes.txt").write_text("not a spec\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "readme.txt").write_text("hi\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "one")
    _git(repo, "tag", "v1")
    _git(repo, "push", "-u", "origin", "work")
    (specs / "templates" / "deploy.yml").write_text("v: working tree\n")
    (specs / "templates" / "new.yml").write_text("v: untracked\n")
    return repo


def test_a_tag_reads_its_commit_not_the_working_tree(clone: Path) -> None:
    source = GitSource.resolve("v1", cwd=clone)

    assert source.sha == _git(clone, "rev-parse", "v1")
    files = source.files(Path(".untaped/awx/templates"))
    assert files == [
        ".untaped/awx/templates/deploy.yml",
        ".untaped/awx/templates/nested/b.yaml",
    ]
    assert source.read_text(files[0]) == "v: 1\n"
    assert source.label(files[0]) == "v1:.untaped/awx/templates/deploy.yml"


def test_paths_resolve_from_the_working_directory(clone: Path) -> None:
    source = GitSource.resolve("v1", cwd=clone / ".untaped")

    assert source.files(Path("awx/templates/deploy.yml")) == [".untaped/awx/templates/deploy.yml"]
    assert source.files(clone / ".untaped/awx/templates/nested") == [
        ".untaped/awx/templates/nested/b.yaml"
    ]


def test_pushed_head_resolves_to_its_commit(clone: Path) -> None:
    assert GitSource.resolve("HEAD", cwd=clone).sha == _git(clone, "rev-parse", "HEAD")


def test_an_unpushed_head_is_refused(clone: Path) -> None:
    _git(clone, "commit", "--allow-empty", "-m", "two")

    with pytest.raises(ConfigError, match="is not pushed"):
        GitSource.resolve("HEAD", cwd=clone)


def test_a_detached_head_names_the_flag(clone: Path) -> None:
    _git(clone, "checkout", "--detach")

    with pytest.raises(ConfigError, match="pass --source-ref a branch, tag or commit"):
        GitSource.resolve("HEAD", cwd=clone)


@pytest.mark.parametrize(
    ("path", "message"),
    [
        (".untaped/awx/templates/new.yml", "does not exist at v1"),
        (".untaped/awx/templates/notes.txt/x", "does not exist at v1"),
        ("../elsewhere", "outside the repository"),
    ],
)
def test_missing_and_outside_paths_are_refused(clone: Path, path: str, message: str) -> None:
    source = GitSource.resolve("v1", cwd=clone)

    with pytest.raises(ConfigError, match=message):
        source.files(Path(path))


def test_a_directory_without_documents_is_refused(clone: Path) -> None:
    source = GitSource.resolve("v1", cwd=clone)

    with pytest.raises(ConfigError, match=r"no \.yml/\.yaml files under docs at v1"):
        source.files(Path("docs"))


def test_unknown_refs_and_non_repositories_are_refused(clone: Path, tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="--source-ref nope: not a commit"):
        GitSource.resolve("nope", cwd=clone)
    outside = tmp_path / "plain"
    outside.mkdir()
    with pytest.raises(ConfigError, match="not inside a git repository"):
        GitSource.resolve("v1", cwd=outside)
