"""S32: a user's hostile global git config changes nothing the store does.

The store's explicit ``--prune``/``--no-prune`` and its repo-scope policy
beat ``fetch.prune``, ``fetch.pruneTags``, ``fetch.unpackLimit``, ``gc.auto``,
``gc.pruneExpire`` and ``maintenance.auto`` set globally, while a user's own
fetch in a store worktree still honours them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from git.conftest import all_refs, git, objects
from untaped.testing.git import GitRemote, git_remote, hostile_git_home
from untaped_git.errors import StoreError
from untaped_git.infrastructure import store as store_module
from untaped_git.infrastructure.store import RepoStore


def _store(base: Path, remote: GitRemote, plugin: str = "workspace") -> RepoStore:
    return RepoStore(
        base / "store" / "git.example" / "app.git",
        url=remote.url,
        plugin=plugin,
        error=StoreError,
        warn=lambda _text: None,
        sleep=lambda _seconds: None,
    )


def _exercise(base: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[str]]]:
    """Run every store writer once on a fresh remote; each writer's command lines.

    After each writer, workspace's unpushed local tag still exists and no
    loose object is left.
    """
    remote = git_remote(base, host=f"{base.name}.example")  # one URL per run
    remote.branch("gone")
    remote.tag("v1")
    store = _store(base, remote)
    seen: list[list[str]] = []
    real = store_module.run_git

    def spy(args: Any, **kwargs: Any) -> Any:
        seen.append([str(a).replace(str(base), "<base>").replace(base.name, "<run>") for a in args])
        return real(args, **kwargs)

    monkeypatch.setattr(store_module, "run_git", spy)
    tree = base / "trees" / "work"
    steps: list[tuple[str, Any]] = [
        ("ensure", store.ensure),
        ("fetch", lambda: store.fetch(branches=["*"], tags=["*"], prune=True)),
        ("tag", lambda: git(store.path, "tag", "wip-local", "refs/remotes/origin/main")),
        ("worktree_add", lambda: store.worktree_add(tree, "refs/remotes/origin/main", branch="w")),
        ("write_worktree_config", lambda: store.write_worktree_config(tree, profile="work")),
        ("upstream", lambda: (remote.delete_branch("gone"), remote.commit("n.txt", "n\n"))),
        ("refetch", lambda: store.fetch(branches=["*"], tags=["*"], prune=True)),
        ("prefetch", lambda: store.prefetch(history=["refs/remotes/origin/*"])),
        ("prefetched", lambda: store.prefetched(trees=["refs/remotes/origin/main"])),
        ("checkout", lambda: store.checkout(tree, "refs/remotes/origin/main")),
        ("delete_refs", lambda: store.delete_refs(["heads/none"])),
        # Another plugin lets go; workspace's refs, tag and worktree keep the repo.
        ("release", lambda: _store(base, remote, "github").release()),
    ]
    lines: list[tuple[str, list[str]]] = []
    for name, step in steps:
        seen.clear()
        step()
        lines.extend((name, argv) for argv in seen)
        refs = all_refs(store.path)
        if name not in ("ensure", "fetch"):
            assert "refs/tags/wip-local" in refs, name
        assert objects(store.path)["count"] == 0, name
    monkeypatch.setattr(store_module, "run_git", real)
    config = git(store.path, "config", "--local", "--list").lower()
    assert "fetch.prune" not in config
    assert "fetch.prunetags" not in config
    assert git(store.path, "config", "gc.pruneExpire").strip() == "2.weeks.ago"
    assert "refs/remotes/origin/gone" not in all_refs(store.path)
    return lines


def test_every_writer_ignores_hostile_globals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = _exercise(tmp_path / "clean", monkeypatch)
    hostile_git_home()
    hostile = _exercise(tmp_path / "hostile", monkeypatch)

    assert hostile == clean
    assert {name for name, _ in clean} >= {
        "ensure",
        "fetch",
        "worktree_add",
        "write_worktree_config",
        "refetch",
        "prefetch",
        "checkout",
        "release",
    }


def test_twelve_fetches_still_repack(tmp_path: Path) -> None:
    hostile_git_home()
    remote = git_remote(tmp_path)
    store = _store(tmp_path, remote, plugin="github")
    for index in range(12):
        remote.commit(f"f{index}.txt", f"{index}\n")
        store.fetch(branches=["main"])
        counts = objects(store.path)
        assert counts["count"] == 0
        assert counts["packs"] <= 11


def test_a_users_own_fetch_still_prunes(tmp_path: Path) -> None:
    hostile_git_home()
    remote = git_remote(tmp_path)
    remote.branch("gone")
    store = _store(tmp_path, remote)
    store.fetch(branches=["*"], tags=["*"], prune=True)
    tree = tmp_path / "trees" / "work"
    store.worktree_add(tree, "refs/remotes/origin/main", branch="w")
    store.write_worktree_config(tree, profile=None)
    remote.delete_branch("gone")

    git(tree, "fetch", "--quiet", bare=False)

    assert "refs/remotes/origin/gone" not in all_refs(store.path)


def test_a_handle_still_reads_what_gc_left_unreachable(tmp_path: Path) -> None:
    """A global ``gc.pruneExpire=now`` cannot prune what an open handle reads.

    Git 2.43 through 2.55 keep every object of a promisor pack when they
    repack, whatever ``gc.pruneExpire`` says, so today this passes without the
    store's repo-scope key too; the key, and this test, cover a git that prunes them.
    """
    hostile_git_home()
    remote = git_remote(tmp_path)
    topic = remote.commit("notes.txt", "kept\n", branch="topic")
    store = _store(tmp_path, remote)
    store.fetch(branches=["*"], prune=True)
    handle = store.prefetched(trees=["refs/remotes/origin/topic"])
    # Another process prunes the ref the handle read, then the maintenance runs.
    remote.delete_branch("topic")
    _store(tmp_path, remote).fetch(branches=["*"], prune=True)
    assert "refs/remotes/origin/topic" not in all_refs(store.path)
    git(store.path, "maintenance", "run", "--task=gc", "--quiet")

    assert handle.run(["cat-file", "-p", f"{topic}:notes.txt"]).text == "kept\n"
