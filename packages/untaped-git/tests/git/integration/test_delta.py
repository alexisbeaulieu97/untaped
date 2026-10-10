"""S41: a fetch returns what it added, moved and pruned in the caller's namespace."""

from __future__ import annotations

from git.conftest import StoreFor
from untaped.testing.git import GitRemote
from untaped_git.domain.delta import RefMove


def _remote_with_old_and_tag(remote: GitRemote) -> dict[str, str]:
    main = remote.oid("main")
    remote.branch("old")
    remote.tag("v1")
    return {"heads/main": main, "heads/old": main, "tags/v1": main}


def test_globs(remote: GitRemote, store_for: StoreFor) -> None:
    first = _remote_with_old_and_tag(remote)
    store = store_for("github")

    assert dict(store.fetch(branches=["*"], tags=["*"]).added) == first

    moved = remote.commit("a.txt", "a\n")
    remote.delete_branch("old")
    new = remote.branch("new")
    delta = store.fetch(branches=["*"], tags=["*"], prune=True)

    assert dict(delta.moved) == {"heads/main": RefMove(first["heads/main"], moved)}
    assert dict(delta.added) == {"heads/new": new}
    assert dict(delta.pruned) == {"heads/old": first["heads/old"]}
    assert not store.fetch(branches=["*"], tags=["*"], prune=True)


def test_explicit_names(remote: GitRemote, store_for: StoreFor) -> None:
    first = _remote_with_old_and_tag(remote)
    store = store_for("github")
    assert dict(store.fetch(branches=["main", "old"], tags=["v1"]).added) == first

    moved = remote.commit("a.txt", "a\n")
    remote.delete_branch("old")
    new = remote.branch("new")
    delta = store.fetch(branches=["main", "new"], tags=["v1"], prune=True)

    assert dict(delta.moved) == {"heads/main": RefMove(first["heads/main"], moved)}
    assert dict(delta.added) == {"heads/new": new}
    assert dict(delta.pruned) == {"heads/old": first["heads/old"]}
    assert not store.fetch(branches=["main", "new"], tags=["v1"], prune=True)


def test_plain_clone_layout(remote: GitRemote, store_for: StoreFor) -> None:
    first = _remote_with_old_and_tag(remote)
    store = store_for("workspace")
    assert dict(store.fetch(branches=["*"], tags=["*"]).added) == first

    moved = remote.commit("a.txt", "a\n")
    remote.delete_branch("old")
    delta = store.fetch(branches=["*"], tags=["*"], prune=True)

    assert dict(delta.moved) == {"heads/main": RefMove(first["heads/main"], moved)}
    assert dict(delta.pruned) == {"heads/old": first["heads/old"]}
    assert "tags/v1" not in {**delta.added, **delta.moved, **delta.pruned}
    assert not store.fetch(branches=["*"], tags=["*"], prune=True)
