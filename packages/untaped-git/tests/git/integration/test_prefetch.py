"""S22, S31, S33: blobs are read only through a prefetched handle, one round trip each."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from git.conftest import StoreError, StoreFor, git
from untaped.sdk import ErrorCategory
from untaped.testing.git import GitRemote, git_shim, trace2_events

#: The sweep's pinned grep flags (github's git_corpus), spelled out here.
SWEEP_GREP = ["grep", "-n", "-I", "--fixed-strings", "-e", "needle"]


def _fetch_starts(trace: Path) -> list[list[str]]:
    return [
        e["argv"]
        for e in trace2_events(trace)
        if e.get("event") == "start" and "fetch" in e["argv"] and "--stdin" in e["argv"]
    ]


@pytest.fixture
def seeded(remote: GitRemote) -> GitRemote:
    remote.commit("src/a.py", "needle = 1\n")
    remote.commit("src/b.py", "haystack\nneedle again\n")
    remote.commit("docs/c.md", "needle in docs\n")
    return remote


def test_one_round_trip_then_local(
    seeded: GitRemote,
    store_for: StoreFor,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    tree = git(store.path, "rev-parse", "refs/untaped/github/heads/main^{tree}").strip()
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))

    handle = store.prefetched(trees=[tree], paths=["src"])
    hits = handle.run([*SWEEP_GREP, tree, "--", "src"]).text
    assert len(_fetch_starts(trace)) == 1

    trace.unlink()
    store.prefetched(trees=[tree], paths=["src"])
    assert _fetch_starts(trace) == []
    monkeypatch.delenv("GIT_TRACE2_EVENT")

    clone = tmp_path / "full"
    subprocess.run(["git", "clone", "--quiet", seeded.url, str(clone)], check=True)
    full_tree = git(clone, "rev-parse", "HEAD^{tree}", bare=False).strip()
    expected = git(clone, *SWEEP_GREP, full_tree, "--", "src", bare=False)
    assert hits == expected
    assert "needle in docs" not in hits


def test_a_handle_refuses_store_mechanics(seeded: GitRemote, store_for: StoreFor) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    handle = store.prefetched(trees=["refs/untaped/github/heads/main"], paths=["src"])

    for argv in (["checkout", "main"], ["worktree", "add", "x"], ["fetch", "origin"]):
        with pytest.raises(ValueError, match="never a Prefetched handle"):
            handle.run(argv)
    with pytest.raises(ValueError, match=r"RepoStore\.fetch"):
        store.run(["fetch", "origin"])


def test_an_unprefetched_read_fails_loudly(seeded: GitRemote, store_for: StoreFor) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    probe = git(store.path, "rev-parse", "refs/untaped/github/heads/main:src/a.py").strip()
    honoured = subprocess.run(
        ["git", f"--git-dir={store.path}", "cat-file", "-e", probe],
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        capture_output=True,
        check=False,
    )
    if honoured.returncode == 0:  # it fetched lazily: the prefetch guard is the only check
        pytest.skip("this git ignores GIT_NO_LAZY_FETCH")
    blob = git(store.path, "rev-parse", "refs/untaped/github/heads/main:docs/c.md").strip()

    with pytest.raises(StoreError) as caught:
        store.run(["cat-file", "-p", blob], capture=True)

    assert "prefetched()" in (caught.value.hint or "")


def test_trees_are_peeled(remote: GitRemote, store_for: StoreFor) -> None:
    for version in range(4):
        commit = remote.commit("f.txt", f"version {version}\n")
    store = store_for("github")
    store.fetch(branches=["main"])

    store.prefetched(trees=[commit], paths=["f.txt"])
    missing = git(store.path, "rev-list", "--objects", "--missing=print", commit, "--", "f.txt")
    assert len([line for line in missing.splitlines() if line.startswith("?")]) == 3

    store.prefetch(history=["refs/untaped/github/heads/*"], paths=["f.txt"])
    missing = git(store.path, "rev-list", "--objects", "--missing=print", commit, "--", "f.txt")
    assert not [line for line in missing.splitlines() if line.startswith("?")]


def test_old_git_prefetch_names_the_floor(
    seeded: GitRemote,
    store_for: StoreFor,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings: list[str],
) -> None:
    store = store_for("github")
    store.fetch(branches=["main"])
    git_shim(tmp_path / "bin", monkeypatch, version="2.25.1", refuse_stdin=True)
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))

    with pytest.raises(StoreError) as caught:
        store.prefetch(trees=["refs/untaped/github/heads/main"])

    assert caught.value.category == ErrorCategory.CONFIG
    assert "2.29" in str(caught.value)
    assert "2.29" in (caught.value.hint or "")
    commands = [e["argv"] for e in trace2_events(trace) if e.get("event") == "start"]
    assert not any("maintenance" in argv for argv in commands)
    assert warnings == []


def test_history_refuses_an_option(store_for: StoreFor) -> None:
    with pytest.raises(StoreError) as caught:
        store_for("github").prefetch(history=["--all"])
    assert caught.value.category == ErrorCategory.INVALID


def _missing_paths(store_path: Path, tree: str) -> set[str]:
    listed = git(store_path, "rev-list", "--objects", "--missing=print", tree)
    missing = {line[1:] for line in listed.splitlines() if line.startswith("?")}
    entries = git(store_path, "ls-tree", "-r", tree).splitlines()
    return {entry.split("\t", 1)[1] for entry in entries if entry.split()[2] in missing}


def test_blobs_by_id_fetch_only_those_then_nothing(
    seeded: GitRemote,
    store_for: StoreFor,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_for("ansible")
    store.fetch(branches=["main"])
    main = "refs/untaped/ansible/heads/main"
    blob = git(store.path, "rev-parse", f"{main}:src/a.py").strip()
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))

    handle = store.prefetched(blobs=[blob, blob])

    assert len(_fetch_starts(trace)) == 1
    assert handle.run(["cat-file", "-p", blob]).text == "needle = 1\n"
    assert _missing_paths(store.path, main) == {"README.md", "src/b.py", "docs/c.md"}
    trace.unlink()
    store.prefetch(blobs=[blob])
    assert _fetch_starts(trace) == []


def test_blobs_and_trees_prefetch_together(seeded: GitRemote, store_for: StoreFor) -> None:
    store = store_for("ansible")
    store.fetch(branches=["main"])
    main = "refs/untaped/ansible/heads/main"
    blob = git(store.path, "rev-parse", f"{main}:docs/c.md").strip()

    store.prefetch(trees=[main], paths=["src/a.py"], blobs=[blob])

    assert _missing_paths(store.path, main) == {"README.md", "src/b.py"}


def test_thousands_of_blob_ids_travel_on_stdin(
    remote: GitRemote, store_for: StoreFor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = remote.work / "many"
    folder.mkdir()
    for index in range(2999):
        (folder / f"f{index}.yml").write_text(f"n: {index}\n", encoding="utf-8")
    subprocess.run(["git", "add", "many"], cwd=remote.work, check=True)
    remote.commit("many/last.yml", "n: last\n")  # commits what is staged with it
    store = store_for("ansible")
    store.fetch(branches=["main"])
    main = "refs/untaped/ansible/heads/main"
    blobs = [entry.oid for entry in store.ls_tree(main, ["many"])]
    assert len(blobs) == 3000
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))

    store.prefetch(blobs=blobs)

    assert len(_fetch_starts(trace)) == 1
    assert _missing_paths(store.path, main) == {"README.md"}


def test_a_host_refusing_blobs_by_id_is_unavailable(seeded: GitRemote, store_for: StoreFor) -> None:
    store = store_for("ansible")
    store.fetch(branches=["main"])
    blob = git(store.path, "rev-parse", "refs/untaped/ansible/heads/main:src/a.py").strip()
    seeded.refuse_by_oid()

    with pytest.raises(StoreError) as caught:
        store.prefetch(blobs=[blob])

    assert caught.value.category == ErrorCategory.UNAVAILABLE
    assert "refused to send blobs by object id" in str(caught.value)


@pytest.mark.parametrize("oid", ["main", "A" * 40])
def test_blobs_refuses_what_is_not_an_object_id(store_for: StoreFor, oid: str) -> None:
    store = store_for("ansible")
    with pytest.raises(StoreError) as caught:
        store.prefetch(blobs=[oid])
    assert caught.value.category == ErrorCategory.INVALID
    assert not store.path.exists()  # refused before the repo is made


def test_blobs_refuses_an_id_of_the_other_object_format(
    seeded: GitRemote, store_for: StoreFor
) -> None:
    with pytest.raises(StoreError) as caught:
        store_for("ansible").prefetch(blobs=["a" * 64])
    assert caught.value.category == ErrorCategory.INVALID


def test_a_missing_blob_is_listed_without_a_lazy_fetch(
    seeded: GitRemote, store_for: StoreFor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = store_for("ansible")
    store.fetch(branches=["main"])
    blob = git(store.path, "rev-parse", "refs/untaped/ansible/heads/main:src/a.py").strip()
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))

    # only gits older than 2.38 would fetch here (mktree did): a guard against regressions
    assert store._missing_blobs([blob]) == [blob]

    starts = [e["argv"] for e in trace2_events(trace) if e.get("event") == "start"]
    assert len(starts) == 1  # rev-list alone: no lazy fetch child


def test_prefetched_needs_something_to_read(store_for: StoreFor) -> None:
    with pytest.raises(ValueError, match="reads nothing"):
        store_for("ansible").prefetched()
