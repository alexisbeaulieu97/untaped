"""Integration tests for GitRunner — uses real git on a tmp_path bare repo."""

import shutil
import subprocess
from pathlib import Path

import pytest

from untaped_workspace.errors import GitError
from untaped_workspace.infrastructure import GitRunner

pytestmark = pytest.mark.integration

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
    first = runner.ensure_bare(f"file://{upstream}", cache_dir=cache)
    assert first.created is True
    assert first.path.is_dir()
    assert (first.path / "HEAD").is_file()
    # second call is a no-op
    second = runner.ensure_bare(f"file://{upstream}", cache_dir=cache)
    assert second.created is False
    assert second.path == first.path


def test_clone_with_reference(tmp_path: Path, upstream: Path) -> None:
    cache = tmp_path / "cache"
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=cache).path
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

    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=cache).path
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


def test_checkout_branch_checks_out_remote_branch_after_fetch(
    tmp_path: Path,
    upstream: Path,
) -> None:
    runner = GitRunner()
    cache = tmp_path / "cache"
    seed = tmp_path / "_seed_checkout"
    subprocess.run(["git", "clone", str(upstream), str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "commit.gpgsign", "false"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "checkout", "-b", "develop"], check=True, capture_output=True
    )
    (seed / "develop.txt").write_text("develop")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "--no-gpg-sign", "-m", "develop"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "origin", "develop"],
        check=True,
        capture_output=True,
    )

    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=cache).path
    workspace_repo = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=workspace_repo, bare=bare)
    subprocess.run(
        ["git", "-C", str(workspace_repo), "config", "checkout.guess", "false"],
        check=True,
    )

    runner.fetch(workspace_repo)
    runner.checkout_branch(workspace_repo, branch="develop")

    head = subprocess.run(
        ["git", "-C", str(workspace_repo), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "develop"
    tracking_remote = subprocess.run(
        ["git", "-C", str(workspace_repo), "config", "--get", "branch.develop.remote"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracking_merge = subprocess.run(
        ["git", "-C", str(workspace_repo), "config", "--get", "branch.develop.merge"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert tracking_remote == "origin"
    assert tracking_merge == "refs/heads/develop"


def test_fetch_populates_remote_branch_for_single_branch_clone(
    tmp_path: Path,
    upstream: Path,
) -> None:
    runner = GitRunner()
    seed = tmp_path / "_seed_single_branch"
    subprocess.run(["git", "clone", str(upstream), str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "commit.gpgsign", "false"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "checkout", "-b", "develop"], check=True, capture_output=True
    )
    (seed / "develop.txt").write_text("develop")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "--no-gpg-sign", "-m", "develop"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "origin", "develop"],
        check=True,
        capture_output=True,
    )

    workspace_repo = tmp_path / "ws" / "svc-a"
    subprocess.run(
        [
            "git",
            "clone",
            "--single-branch",
            "--branch",
            "main",
            str(upstream),
            str(workspace_repo),
        ],
        check=True,
        capture_output=True,
    )

    runner.fetch(workspace_repo)
    runner.checkout_branch(workspace_repo, branch="develop")

    head = subprocess.run(
        ["git", "-C", str(workspace_repo), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "develop"


def test_checkout_branch_creates_local_branch_when_remote_branch_is_missing(
    tmp_path: Path,
    upstream: Path,
) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    workspace_repo = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=workspace_repo, bare=bare)

    runner.fetch(workspace_repo)
    runner.checkout_branch(workspace_repo, branch="ticket-123")

    head = subprocess.run(
        ["git", "-C", str(workspace_repo), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracking_remote = subprocess.run(
        ["git", "-C", str(workspace_repo), "config", "--get", "branch.ticket-123.remote"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert head == "ticket-123"
    assert tracking_remote.returncode != 0


def test_checkout_branch_creates_local_branch_when_remote_ref_is_not_a_commit(
    tmp_path: Path,
    upstream: Path,
) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    workspace_repo = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=workspace_repo, bare=bare)
    tree = subprocess.run(
        ["git", "-C", str(workspace_repo), "rev-parse", "HEAD^{tree}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(workspace_repo), "update-ref", "refs/remotes/origin/ticket-123", tree],
        check=True,
    )

    runner.checkout_branch(workspace_repo, branch="ticket-123")

    head = subprocess.run(
        ["git", "-C", str(workspace_repo), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracking_remote = subprocess.run(
        ["git", "-C", str(workspace_repo), "config", "--get", "branch.ticket-123.remote"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert head == "ticket-123"
    assert tracking_remote.returncode != 0


def test_checkout_branch_checks_out_existing_local_branch(
    tmp_path: Path,
    upstream: Path,
) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    workspace_repo = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=workspace_repo, bare=bare)
    subprocess.run(
        ["git", "-C", str(workspace_repo), "checkout", "-b", "local-only"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace_repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )

    runner.checkout_branch(workspace_repo, branch="local-only")

    head = subprocess.run(
        ["git", "-C", str(workspace_repo), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "local-only"


def test_checkout_branch_raises_git_error_for_invalid_branch_name(
    tmp_path: Path,
    upstream: Path,
) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    workspace_repo = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=workspace_repo, bare=bare)

    with pytest.raises(GitError):
        runner.checkout_branch(workspace_repo, branch="bad..branch")


def test_status_clean_repo(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    status = runner.status(ws)
    assert status.branch == "main"
    assert not status.dirty
    assert status.ahead == 0
    assert status.behind == 0


def test_status_dirty_working_tree(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
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
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    assert runner.default_branch(bare) == "main"


def test_runner_raises_git_error_on_bad_command(tmp_path: Path) -> None:
    runner = GitRunner()
    not_a_repo = tmp_path / "nope"
    not_a_repo.mkdir()
    with pytest.raises(GitError):
        runner.status(not_a_repo)


# ── read_remote_url / read_current_branch (used by `workspace adopt`) ──────


def test_read_remote_url_returns_origin_url(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    assert runner.read_remote_url(ws) == f"file://{upstream}"


def test_read_remote_url_returns_none_for_missing_remote(tmp_path: Path) -> None:
    runner = GitRunner()
    repo = tmp_path / "lonely"
    repo.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repo)], check=True)
    assert runner.read_remote_url(repo) is None


def test_read_current_branch_returns_branch_name(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    assert runner.read_current_branch(ws) == "main"


def test_read_current_branch_returns_none_when_detached(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    sha = subprocess.run(
        ["git", "-C", str(ws), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(ws), "checkout", "--detach", sha],
        check=True,
        capture_output=True,
    )
    assert runner.read_current_branch(ws) is None
