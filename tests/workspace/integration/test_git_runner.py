"""Integration tests for GitRunner — uses real git on a tmp_path bare repo."""

import shutil
import subprocess
from pathlib import Path

import pytest

from untaped.capabilities.workspace.errors import GitError
from untaped.capabilities.workspace.infrastructure import GitRunner

pytestmark = pytest.mark.integration

if shutil.which("git") is None:
    pytest.skip("git not on PATH", allow_module_level=True)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _configure_identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "tag.gpgsign", "false")


def _commit(repo: Path, filename: str, content: str, message: str) -> None:
    (repo / filename).write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "--no-gpg-sign", "-m", message)


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
    # The cache only accelerates the clone: objects are copied in
    # (``--dissociate``) so later cache pruning/gc can't corrupt it.
    alt = workspace / "svc-a" / ".git" / "objects" / "info" / "alternates"
    assert not alt.exists()


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


def test_prune_blockers_clean_repo(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)

    assert runner.prune_blockers(ws) == ()


def test_prune_blockers_refuses_clean_unpushed_commit(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    _configure_identity(ws)
    _commit(ws, "local.txt", "local", "local")

    assert runner.prune_blockers(ws) == (
        "local commits not reachable from any remote-tracking ref",
    )


def test_prune_blockers_refuses_tag_only_local_commit(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    _configure_identity(ws)
    _git(ws, "checkout", "--detach", "HEAD")
    _commit(ws, "tagged.txt", "tagged", "tagged-only")
    _git(ws, "tag", "local-only")
    _git(ws, "checkout", "main")

    assert runner.prune_blockers(ws) == (
        "local commits not reachable from any remote-tracking ref",
    )


def test_prune_blockers_refuses_stash(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    _configure_identity(ws)
    (ws / "README.md").write_text("stashed")
    _git(ws, "stash", "push", "-m", "keep me")

    assert runner.prune_blockers(ws) == ("stash entries present",)


def test_prune_blockers_allows_local_branch_reachable_from_remote(
    tmp_path: Path, upstream: Path
) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    _configure_identity(ws)
    _git(ws, "checkout", "-b", "local-feature")
    _commit(ws, "feature.txt", "feature", "feature")
    _git(ws, "checkout", "main")
    _git(ws, "merge", "--no-ff", "local-feature", "-m", "merge local feature")
    _git(ws, "push", "origin", "main")

    assert runner.prune_blockers(ws) == ()


def test_prune_blockers_refuses_unreachable_detached_head(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    _configure_identity(ws)
    _git(ws, "checkout", "--detach", "HEAD")
    _commit(ws, "detached.txt", "detached", "detached")

    assert runner.prune_blockers(ws) == (
        "local commits not reachable from any remote-tracking ref",
    )


def test_prune_blockers_refuses_repo_with_no_remote_refs(tmp_path: Path) -> None:
    repo = tmp_path / "lonely"
    repo.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repo)], check=True)
    _configure_identity(repo)
    _commit(repo, "local.txt", "local", "local")

    assert GitRunner().prune_blockers(repo) == (
        "local commits not reachable from any remote-tracking ref",
    )


def test_prune_blockers_allows_unborn_empty_repo(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main", str(repo)], check=True)

    assert GitRunner().prune_blockers(repo) == ()


def test_prune_blockers_allows_ignored_files(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    (ws / ".git" / "info" / "exclude").write_text("ignored.txt\n")
    (ws / "ignored.txt").write_text("ignored")

    assert runner.prune_blockers(ws) == ()


def test_prune_blockers_refuses_untracked_files(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    (ws / "new.txt").write_text("new")

    assert runner.prune_blockers(ws) == ("dirty working tree",)


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


# ── hardening ────────────────────────────────────────────────────────────────


def _push_upstream_commit(upstream: Path, tmp_path: Path, name: str) -> str:
    seed = tmp_path / f"_seed_{name}"
    subprocess.run(["git", "clone", str(upstream), str(seed)], check=True, capture_output=True)
    _configure_identity(seed)
    _commit(seed, f"{name}.txt", name, name)
    _git(seed, "push", "origin", "main")
    sha = _git(seed, "rev-parse", "HEAD").stdout.strip()
    shutil.rmtree(seed)
    return sha


def test_bare_fetch_refreshes_branches_pushed_after_cache_exists(
    tmp_path: Path, upstream: Path
) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    new_sha = _push_upstream_commit(upstream, tmp_path, "later")

    runner.bare_fetch(bare)

    assert _git(bare, "rev-parse", "refs/heads/main").stdout.strip() == new_sha


def test_git_does_not_fall_through_to_enclosing_repo(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(outer, "init", "--quiet")
    inner = outer / "not-a-clone"
    inner.mkdir()

    with pytest.raises(GitError):
        GitRunner().status(inner)


def test_git_failure_message_omits_argv_paths(tmp_path: Path) -> None:
    dest = tmp_path / "ws" / "svc-a"
    bare = tmp_path / "bare.git"
    with pytest.raises(GitError) as excinfo:
        GitRunner().clone_with_reference(
            url=f"file://{tmp_path / 'missing.git'}", dest=dest, bare=bare
        )
    message = str(excinfo.value)
    assert message.startswith("git clone failed: ")
    assert str(dest) not in message
    assert "--reference" not in message


def test_status_reports_upstream(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    assert runner.status(ws).upstream == "origin/main"

    _git(ws, "checkout", "-b", "local-only")
    assert runner.status(ws).upstream is None


def test_ff_only_pull_uses_configured_upstream(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    _git(ws, "checkout", "-b", "work", "--track", "origin/main")
    new_sha = _push_upstream_commit(upstream, tmp_path, "upstream-change")
    runner.fetch(ws)

    runner.ff_only_pull(ws, branch="work")

    assert _git(ws, "rev-parse", "HEAD").stdout.strip() == new_sha


def test_has_branch_checks_local_and_origin(tmp_path: Path, upstream: Path) -> None:
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    ws = tmp_path / "ws" / "svc-a"
    runner.clone_with_reference(url=f"file://{upstream}", dest=ws, bare=bare)
    _git(ws, "branch", "local-only")

    assert runner.has_branch(ws, branch="main") is True
    assert runner.has_branch(ws, branch="local-only") is True
    _git(ws, "checkout", "--quiet", "local-only")
    _git(ws, "branch", "-D", "main")
    assert runner.has_branch(ws, branch="main") is True  # origin/main
    assert runner.has_branch(ws, branch="mian") is False


def test_clone_survives_cache_prune_and_gc_of_deleted_branch(
    tmp_path: Path, upstream: Path
) -> None:
    """Objects only reachable from a deleted cache branch must not break clones."""
    seed = tmp_path / "_seed_gc"
    subprocess.run(["git", "clone", str(upstream), str(seed)], check=True, capture_output=True)
    _configure_identity(seed)
    _git(seed, "checkout", "-b", "feature")
    _commit(seed, "feature.txt", "f", "feature work")
    _git(seed, "push", "origin", "feature")

    runner = GitRunner()
    url = f"file://{upstream}"
    bare = runner.ensure_bare(url, cache_dir=tmp_path / "cache").path
    runner.bare_fetch(bare)
    clone = tmp_path / "ws" / "svc"
    runner.clone_with_reference(url=url, dest=clone, bare=bare, branch="feature")

    _git(seed, "push", "origin", "--delete", "feature")
    runner.bare_fetch(bare)
    _git(bare, "gc", "--prune=now")

    assert _git(clone, "fsck", "--full", check=False).returncode == 0
    assert _git(clone, "log", "-1", "--format=%s").stdout.strip() == "feature work"


def test_bare_cache_disables_auto_gc_and_pruning(tmp_path: Path, upstream: Path) -> None:
    """Clones made before ``--dissociate`` still borrow cache objects."""
    runner = GitRunner()
    bare = runner.ensure_bare(f"file://{upstream}", cache_dir=tmp_path / "cache").path
    _git(bare, "config", "--unset", "gc.pruneExpire", check=False)
    runner.bare_fetch(bare)
    assert _git(bare, "config", "gc.pruneExpire").stdout.strip() == "never"
    assert _git(bare, "config", "gc.auto").stdout.strip() == "0"
