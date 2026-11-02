"""Packs installed from a git URL, fetched through the git plugin's repo store.

Each pack URL is a store repo (``untaped_git.api.RepoStore``) in recipe's own
namespace, so the credentials a host plugin supplies (``GitHost``), untaped's
proxy and the store's object policy reach a pack like any other repo. A fetch
names only what it reads: the remote's default branch, the asked ``rev`` as a
branch or tag, or, when it is neither, every branch and tag so a commit id
resolves. The commit's tree is exported into a folder the caller copies from;
the store keeps the repo between ``add`` and ``sync``, and recipe releases it
once no installed pack uses the URL.
"""

from __future__ import annotations

import io
import tarfile
from collections.abc import Iterable
from pathlib import Path

from untaped.sdk import ErrorCategory, UntapedError, UsageError, attribution
from untaped_git.api import RepoStore, check_names, default_branch, store_key
from untaped_recipe.errors import PackFetchError

_GIT_URL_PREFIXES = ("https://", "git@", "ssh://")


def is_git_url(value: str) -> bool:
    """Return true when the CLI should treat ``value`` as a git URL."""
    return value.startswith(_GIT_URL_PREFIXES)


def fetch_pack_source(url: str, *, rev: str | None, dest: Path, plugin: object) -> str:
    """Fetch ``url`` at ``rev`` (its default branch when ``None``) and export it into ``dest``.

    Returns the commit exported. ``rev`` is a branch, a tag (a branch wins,
    as with ``git clone --branch``) or a commit id the remote's branches or
    tags reach. ``plugin`` names recipe, as for any store consumer.
    """
    if rev is not None and (not rev.strip() or rev.startswith("-")):
        raise UsageError(f"invalid --rev: {rev!r}")
    store = RepoStore.for_url(url, plugin=plugin, error=PackFetchError)
    commit = _fetch(store, url, rev)
    store.write_private()
    handle = store.prefetched(trees=[commit])
    archive = handle.run(["archive", "--format=tar", commit]).stdout
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter=_no_links)
    return commit


def release_unused_sources(
    released: Iterable[str], installed: Iterable[str], *, plugin: object
) -> None:
    """Release the store repo of each git URL in ``released`` that no ``installed`` source uses.

    Best effort: the pack change already happened, and the next ``add`` of
    that URL fetches into the kept repo again.
    """
    kept = {store_key(source) for source in installed if is_git_url(source)}
    for url in dict.fromkeys(released):
        if not is_git_url(url) or store_key(url) in kept:
            continue
        try:
            store = RepoStore.for_url(url, plugin=plugin, error=PackFetchError)
            if store.has_part():
                store.release()
        except UntapedError:
            continue


def _fetch(store: RepoStore, url: str, rev: str | None) -> str:
    """Fetch what ``rev`` needs into recipe's namespace and return its commit."""
    if rev is None:
        try:
            branch = default_branch(url)
        except UntapedError as exc:  # git's own error: recipe reports its fetches
            raise PackFetchError(str(exc), hint=exc.hint, **attribution(exc)) from exc
        if branch is None:
            raise PackFetchError(
                f"{url} names no default branch; pass --rev", category=ErrorCategory.NOT_FOUND
            )
        store.fetch(branches=[branch], prune=True)
        return _commit(store, store.refs().get(f"heads/{branch}"), url, branch)
    if check_names([rev]) is None and "*" not in rev:
        store.fetch(branches=[rev], tags=[rev], prune=True)
        refs = store.refs()
        found = refs.get(f"heads/{rev}") or refs.get(f"tags/{rev}")
        if found is not None:
            return _commit(store, found, url, rev)
    store.fetch(branches=["*"], tags=["*"], prune=True)
    return _commit(store, rev, url, rev)


def _commit(store: RepoStore, revision: str | None, url: str, rev: str) -> str:
    """The commit ``revision`` (an oid, or a commit id typed as ``rev``) peels to."""
    if revision is not None:
        result = store.run(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{commit}}"],
            capture=True,
            check=False,
        )
        if result.returncode == 0:
            return result.text.strip()
    raise PackFetchError(
        f"{url} has no branch, tag or commit {rev!r}", category=ErrorCategory.NOT_FOUND
    )


def _no_links(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo | None:
    """Extract like ``tarfile.data_filter``, refusing links as a pack's validation does."""
    if member.issym() or member.islnk():
        raise ValueError(f"pack must not contain symlinks: {member.name}")
    return tarfile.data_filter(member, path)
