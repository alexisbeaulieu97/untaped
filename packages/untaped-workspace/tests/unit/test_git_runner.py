"""Integration tests for GitRunner — uses real git on a tmp_path bare repo."""

import shutil
import subprocess
from pathlib import Path

import pytest
from untaped_workspace.errors import GitError
from untaped_workspace.infrastructure import GitRunner

if shutil.which("git") is None:
    pytest.skip("git not on PATH", allow_module_level=True)


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """Create a bare repo with one commit; return its path."""
    bare = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(bare)], check=True)
    seed = tmp_path / "_seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "commit.gpgsign", "false"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "tag.gpgsign", "false"], check=True)
    (seed / "README.md").write_text("hi")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "--no-gpg-sign", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "origin", "main"], check=True, capture_output=True
    )
    shutil.rmtree(seed)
    return bare


def test_ensure_bare_clones_first_time_and_caches(tmp_path: Path, upstream: Path) -> None:
    cache = tmp_path / "cache"
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=cache)
    assert bare.is_dir()
    assert (bare / "HEAD").is_file()
    # second call is a no-op
    bare2 = runner.ensure_bare(f"file://{upstream}", cache_dir=cache)
    assert bare2 == bare


def test_clone_with_reference(tmp_path: Path, upstream: Path) -> None:
    cache = tmp_path / "cache"
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=cache)
    workspace = tmp_path / "ws"
    runner.clone_with_reference(url=f"file://{upstream}", dest=workspace / "svc-a", bare=bare)
    assert (workspace / "svc-a" / ".git").is_dir()
    # Reference points to the bare's objects
    alt = workspace / "svc-a" / ".git" / "objects" / "info" / "alternates"
    assert alt.is_file()


def test_clone_with_reference_specific_branch(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    cache = tmp_path / "cache"
    # Push a `develop` branch to upstream first.
    seed = tmp_path / "_seed2"
    subprocess.run(["git", "clone", str(upstream), str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "commit.gpgsign", "false"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "tag.gpgsign", "false"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "checkout", "-b", "develop"], check=True, capture_output=True
    )
    (seed / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "--no-gpg-sign", "-m", "dev"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "origin", "develop"], check=True, capture_output=True
    )

    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=cache)
    runner.bare_fetch(bare)

    workspace = tmp_path / "ws"
    runner.clone_with_reference(
        url=f"file://{upstream}",
        dest=workspace / "svc-a",
        bare=bare,
        branch="develop",
    )
    head = subprocess.run(
        ["git", "-C", str(workspace / "svc-a"), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "develop"


def test_status_clean_repo(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache")
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    status = runner.status(ws)
    assert status.branch == "main"
    assert not status.dirty
    assert status.ahead == 0
    assert status.behind == 0


def test_status_dirty_working_tree(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache")
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    (ws / "README.md").write_text("changed")
    (ws / "newfile.txt").write_text("new")
    status = runner.status(ws)
    assert status.dirty
    assert status.modified >= 1
    assert status.untracked >= 1


def test_default_branch_reads_bare_head(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache")
    assert runner.default_branch(bare) == "main"


def test_runner_raises_git_error_on_bad_command(tmp_path: Path) -> None:
    runner = GitRunner()
    not_a_repo = tmp_path / "nope"
    not_a_repo.mkdir()
    with pytest.raises(GitError):
        runner.status(not_a_repo)
