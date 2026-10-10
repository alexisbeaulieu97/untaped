"""Ansible's dependency sources in the git plugin's repo store.

Each source repo is a store repo (``RepoStore.for_url(url, plugin=SPEC)``),
which workspace and github may share: ansible's refs live in its own
namespace, ``refs/untaped/ansible/heads/*`` and ``refs/untaped/ansible/tags/*``,
and its private file is the repo's ``untaped-ansible.json``. The store fetches
full history blobless and holds the credentials (github's ``GithubHost``
answers for the GitHub host), so nothing here sees a token, a depth or a filter.

Dependency files are read through one ``prefetched()`` handle per repo: one
blob fetch for every changed ref's files, then one local ``cat-file --batch``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from untaped.sdk import (
    GitCommandError,
    UntapedError,
    atomic_write,
    attribution,
    git_toplevel,
    run_git,
)
from untaped_ansible.domain.payloads import GitRef
from untaped_ansible.errors import GitCacheError as GitCacheError
from untaped_git.api import (
    Released,
    Removed,
    RepoStore,
    check_names,
    default_branch,
    ls_remote,
)

DEFAULT_TIMEOUT = 60.0
# ``cat-file --batch`` reads many blobs in one process: allow extra time per blob.
PER_FILE_READ_TIMEOUT = 1.0


class GitSourceStore:
    """Fetch source refs into the repo store and read dependency files from them."""

    def fetch(self, url: str, refs: Sequence[GitRef]) -> None:
        """Bring ``refs`` (each at its probed commit) into ansible's namespace of ``url``.

        Refs the namespace already holds at that commit are not fetched, so a
        refresh whose refs did not move costs no network call here.
        """
        if not refs:
            return
        store = self._store(url)
        held = _peeled_refs(store)
        stale = [ref for ref in refs if held.get(f"{ref.kind}/{ref.name}") != ref.sha]
        if stale:
            store.fetch(
                branches=[ref.name for ref in stale if ref.kind == "heads"],
                tags=[ref.name for ref in stale if ref.kind == "tags"],
            )
        if store.exists() and not store.private_file.is_file():
            # Its presence tells the store's report and a release that ansible uses the repo.
            atomic_write(store.private_file, json.dumps({"url": url}) + "\n")

    def release(self, url: str) -> Released | Removed | None:
        """Give up ansible's part of ``url``'s store repo (``None`` when there is none).

        The repo goes when no other plugin holds it; otherwise :class:`Released`
        names who kept it. Waits on the repo's lock, so a fetch in progress
        finishes first.
        """
        store = self._store(url)
        if not store.exists():
            return None
        return store.release()

    def refusal(self, ref: GitRef) -> str | None:
        """Why the store refuses ``ref``'s name (git allows some, like ``-wip``), else ``None``."""
        if check_names([ref.name]) is None:
            return None
        return "git allows this name, but the repo store cannot hold it"

    def ls_remote(self, url: str, *, patterns: list[str]) -> dict[str, str]:
        """``ref → commit`` of ``url``'s refs matching ``patterns``, with untaped's credentials."""
        try:
            return ls_remote(url, patterns)
        except UntapedError as exc:
            raise GitCacheError(str(exc), **attribution(exc)) from exc

    def default_branch(self, url: str) -> str | None:
        """The branch ``url``'s ``HEAD`` points at, or ``None``."""
        try:
            return default_branch(url)
        except UntapedError as exc:
            raise GitCacheError(str(exc), **attribution(exc)) from exc

    def read_files(
        self, url: str, shas: Sequence[str], paths: Sequence[str]
    ) -> dict[str, dict[str, str]]:
        """``sha → path → content`` for the blobs among ``paths`` at each commit of ``shas``.

        The store's tree listings say which paths exist as blobs; only those
        are prefetched, in one handle for every commit, then one
        ``cat-file --batch`` reads them all. Absent paths are simply omitted:
        existence comes from the listings, never from Git error text, so any
        non-zero exit is a real failure.
        """
        wanted = list(dict.fromkeys(paths))
        commits = list(dict.fromkeys(shas))
        if not wanted or not commits:
            return {sha: {} for sha in commits}
        store = self._store(url)
        blobs_at = {
            sha: {
                entry.path: entry.oid
                for entry in store.ls_tree(sha, wanted)
                if entry.type == "blob" and entry.path in wanted
            }
            for sha in commits
        }
        found = sorted({path for blobs in blobs_at.values() for path in blobs})
        blob_ids = list(dict.fromkeys(oid for blobs in blobs_at.values() for oid in blobs.values()))
        if not blob_ids:
            return {sha: {} for sha in commits}
        handle = store.prefetched(trees=[sha for sha in commits if blobs_at[sha]], paths=found)
        result = handle.run(
            ["cat-file", "--batch"],
            stdin="".join(f"{blob}\n" for blob in blob_ids).encode(),
            timeout=DEFAULT_TIMEOUT + PER_FILE_READ_TIMEOUT * len(blob_ids),
        )
        contents = _parse_cat_file_batch(result.stdout, blob_ids)
        return {
            sha: {path: contents[blob] for path, blob in blobs.items()}
            for sha, blobs in blobs_at.items()
        }

    def _store(self, url: str) -> RepoStore:
        from untaped_ansible import SPEC  # noqa: PLC0415  # the package imports this module

        return RepoStore.for_url(url, plugin=SPEC, error=GitCacheError)


def _peeled_refs(store: RepoStore) -> dict[str, str]:
    """Ansible's refs in ``store``, ``heads/<b>``/``tags/<t>`` → commit (annotated tags peeled)."""
    if not store.exists():
        return {}
    listing = store.run(
        ["for-each-ref", "--format=%(objectname) %(*objectname) %(refname)", *store.roots],
        capture=True,
    ).text
    held: dict[str, str] = {}
    for line in listing.splitlines():
        oid, peeled, ref = line.split(" ", 2)
        relative = store.relative(ref)
        if relative is not None:
            held[relative] = peeled or oid
    return held


def local_remote_url(
    path: Path, *, git: str = "git", timeout: float = DEFAULT_TIMEOUT
) -> str | None:
    """Return the ``origin`` remote URL (else the first remote URL) for ``path``.

    Delegates to ``git config`` so linked worktrees, config includes, and
    repeated keys resolve exactly as Git itself resolves them. Returns
    ``None`` unless ``path`` is the top level of a checkout with a remote: a
    subdirectory (say ``roles/web`` in a monorepo) is not the enclosing repo,
    whose indexed edges its local overlay would otherwise replace. Inherited
    ``GIT_DIR``/``GIT_WORK_TREE``/``GIT_INDEX_FILE`` are dropped (by
    ``run_git``) so the lookup always targets ``path``.
    """
    cwd = path if path.is_dir() else path.parent

    def lookup(*args: str) -> str:
        try:
            result = run_git(args, cwd=cwd, git=git, timeout=timeout, capture=True, check=False)
        except GitCommandError:
            return ""
        return result.text.strip() if result.returncode == 0 else ""

    try:
        top = git_toplevel(cwd, git=git, timeout=timeout)
    except GitCommandError:
        return None
    if top != cwd.resolve():
        return None
    lines = lookup("config", "--get", "remote.origin.url").splitlines()
    if lines:
        return lines[0].strip() or None
    lines = lookup("config", "--get-regexp", r"^remote\..*\.url$").splitlines()
    if lines:
        return lines[0].split(maxsplit=1)[-1].strip() or None
    return None


def _parse_cat_file_batch(output: bytes, blob_ids: list[str]) -> dict[str, str]:
    """Split ``git cat-file --batch`` output into decoded blob contents."""
    contents: dict[str, str] = {}
    offset = 0
    for blob in blob_ids:
        header_end = output.find(b"\n", offset)
        if header_end < 0:
            raise GitCacheError(f"git cat-file --batch returned truncated output for {blob}")
        fields = output[offset:header_end].decode("utf-8", errors="replace").split()
        if len(fields) != 3 or fields[1] != "blob":
            raise GitCacheError(f"git cat-file --batch could not read {blob}: {' '.join(fields)}")
        size = int(fields[2])
        start = header_end + 1
        end = start + size
        # Each object is ``<header>\n<size bytes>\n``; anything shorter is a
        # cut-off stream, never a smaller file.
        if end + 1 > len(output) or output[end : end + 1] != b"\n":
            raise GitCacheError(f"git cat-file --batch returned truncated output for {blob}")
        contents[fields[0]] = output[start:end].decode("utf-8", errors="replace")
        offset = end + 1
    return contents
