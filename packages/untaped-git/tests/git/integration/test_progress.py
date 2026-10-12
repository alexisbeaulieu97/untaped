"""A store call reports its phases through the progress handle a caller passes."""

from __future__ import annotations

from git.conftest import StoreFor
from untaped.testing.git import GitRemote

NAMES = [f"b{index:03d}" for index in range(60)]

type _Update = tuple[str | None, str | None, int | None, int | None]


class _Recorder:
    def __init__(self) -> None:
        self.updates: list[_Update] = []

    def update(
        self,
        message: str | None = None,
        *,
        phase: str | None = None,
        done: int | None = None,
        total: int | None = None,
    ) -> None:
        self.updates.append((message, phase, done, total))


def test_fetch_lists_then_counts_the_refs_it_fetches(
    remote: GitRemote, store_for: StoreFor
) -> None:
    remote.spread(NAMES)
    progress = _Recorder()

    store_for("github").fetch(branches=NAMES, progress=progress)

    assert progress.updates == [
        (None, "listing refs", None, None),
        (None, "fetching refs", 0, 60),
        (None, "fetching refs", 50, 60),
        (None, "fetching refs", 60, 60),
    ]


def test_a_globs_only_fetch_is_one_phase_without_counts(
    remote: GitRemote, store_for: StoreFor
) -> None:
    remote.commit("a.txt", "a\n")
    progress = _Recorder()

    store_for("workspace").fetch(branches=["*"], tags=["*"], progress=progress)

    assert progress.updates == [(None, "fetching refs", None, None)]


def test_prefetch_counts_the_blobs_it_fetches_and_nothing_when_none_is_missing(
    remote: GitRemote, store_for: StoreFor
) -> None:
    remote.commit("a.txt", "a\n")
    remote.commit("b.txt", "b\n")
    store = store_for("github")
    store.fetch(branches=["main"])
    progress = _Recorder()

    store.prefetch(trees=["refs/untaped/github/heads/main"], progress=progress)
    (_, phase, first, total), (_, _, last, _) = progress.updates
    assert (phase, first, last) == ("fetching blobs", 0, total)
    assert total is not None and total >= 2  # a.txt and b.txt at least

    again = _Recorder()
    store.prefetch(trees=["refs/untaped/github/heads/main"], progress=again)
    assert again.updates == []


def test_prefetched_forwards_the_handle(remote: GitRemote, store_for: StoreFor) -> None:
    remote.commit("a.txt", "a\n")
    store = store_for("github")
    store.fetch(branches=["main"])
    progress = _Recorder()

    store.prefetched(trees=["refs/untaped/github/heads/main"], progress=progress)

    assert [phase for _, phase, _, _ in progress.updates] == ["fetching blobs", "fetching blobs"]
