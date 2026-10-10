"""Unit tests for ansible's store adapter: ``cat-file --batch`` parsing and read timeouts.

Real-git behaviour lives in ``tests/ansible/integration/test_git_store.py``;
credentials, prefetch and locking belong to the git plugin.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from untaped.sdk import GitResult
from untaped_ansible.infrastructure.git_store import GitCacheError, GitSourceStore
from untaped_git.api import TreeEntry


class FakeHandle:
    def __init__(self, stdout: bytes, timeouts: list[float | None]) -> None:
        self._stdout = stdout
        self._timeouts = timeouts

    def run(self, argv: Sequence[str], **kwargs: Any) -> GitResult:
        assert argv == ["cat-file", "--batch"]
        self._timeouts.append(kwargs.get("timeout"))
        return GitResult(returncode=0, stdout=self._stdout, stderr="")


class FakeStore:
    """A store repo whose commits hold ``blobs`` (path → blob id; ``trees`` per commit
    when they differ); ``cat-file`` answers ``stdout``."""

    def __init__(
        self,
        blobs: dict[str, str],
        stdout: bytes,
        trees: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.trees = trees or {}
        self.blobs = blobs
        self.stdout = stdout
        self.timeouts: list[float | None] = []
        self.prefetches: list[tuple[list[str], list[str]]] = []

    def ls_tree(self, tree: str, paths: Sequence[str] = ()) -> list[TreeEntry]:
        return [
            TreeEntry(mode="100644", type="blob", oid=oid, path=path)
            for path, oid in self.trees.get(tree, self.blobs).items()
        ]

    def prefetched(self, *, trees: Sequence[str], paths: Sequence[str] = ()) -> FakeHandle:
        self.prefetches.append((list(trees), list(paths)))
        return FakeHandle(self.stdout, self.timeouts)


def _source(monkeypatch: pytest.MonkeyPatch, store: FakeStore) -> GitSourceStore:
    source = GitSourceStore()
    monkeypatch.setattr(source, "_store", lambda _url: store)
    return source


@pytest.mark.parametrize(
    "batch_stdout",
    [
        b"b1 blob 10\nabc\n",  # content shorter than the header size
        b"b1 blob 3\nabc\nb2 blob 5\nxy",  # second object cut off mid-content
        b"b1 blob 3\nabcX",  # missing the trailing newline after the content
    ],
)
def test_read_files_rejects_truncated_cat_file_output(
    monkeypatch: pytest.MonkeyPatch, batch_stdout: bytes
) -> None:
    source = _source(monkeypatch, FakeStore({"a.yml": "b1", "b.yml": "b2"}, batch_stdout))

    with pytest.raises(GitCacheError, match="truncated"):
        source.read_files("https://github.com/acme/site.git", ["abc123"], ["a.yml", "b.yml"])


def test_read_files_prefetches_only_the_paths_the_tree_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeStore({"a.yml": "b1"}, b"b1 blob 1\nx\n")

    files = _source(monkeypatch, store).read_files("url", ["abc"], ["a.yml", "missing.yml"])

    assert files == {"abc": {"a.yml": "x"}}
    assert store.prefetches == [(["abc"], ["a.yml"])]


def test_read_files_reads_every_commit_through_one_prefetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trees = {"c1": {"a.yml": "b1"}, "c2": {"a.yml": "b1", "b.yml": "b2"}, "c3": {}}
    store = FakeStore({}, b"b1 blob 1\nx\nb2 blob 1\ny\n", trees=trees)

    files = _source(monkeypatch, store).read_files(
        "url", ["c1", "c2", "c3", "c1"], ["a.yml", "b.yml"]
    )

    assert files == {"c1": {"a.yml": "x"}, "c2": {"a.yml": "x", "b.yml": "y"}, "c3": {}}
    assert store.prefetches == [(["c1", "c2"], ["a.yml", "b.yml"])]


def test_read_files_with_nothing_to_read_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore({}, b"")
    source = _source(monkeypatch, store)

    assert source.read_files("url", ["c1"], ["a.yml"]) == {"c1": {}}
    assert source.read_files("url", ["c1"], []) == {"c1": {}}
    assert store.prefetches == []


def test_read_files_timeout_scales_with_number_of_files(monkeypatch: pytest.MonkeyPatch) -> None:
    few = FakeStore({"a.yml": "b0"}, b"b0 blob 1\nx\n")
    _source(monkeypatch, few).read_files("url", ["abc"], ["a.yml"])

    blobs = {f"f{i}.yml": f"b{i}" for i in range(200)}
    many = FakeStore(blobs, b"".join(f"b{i} blob 1\nx\n".encode() for i in range(200)))
    _source(monkeypatch, many).read_files("url", ["abc"], list(blobs))

    (few_timeout,) = few.timeouts
    (many_timeout,) = many.timeouts
    assert few_timeout is not None
    assert many_timeout is not None
    assert few_timeout >= 60
    assert many_timeout > few_timeout
