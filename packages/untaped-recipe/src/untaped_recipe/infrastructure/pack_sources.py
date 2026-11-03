"""Packs installed from a git URL, fetched through the git plugin's repo store.

Each pack URL is a store repo (``untaped_git.api.RepoStore``) in recipe's own
namespace, so the credentials a host plugin supplies (``GitHost``), untaped's
proxy and the store's object policy reach a pack like any other repo. A fetch
names only what it reads: the remote's default branch, or the asked ``rev`` as
the branch or tag the remote lists (a branch wins, as with ``git clone
--branch``); a ``rev`` the remote lists as neither is read as a commit id,
which recipe's branches and tags must reach once every one is fetched. The
commit's tree is written into a folder the caller copies from, exactly as
committed (no ``.gitattributes`` export rules). The store keeps the repo
between ``add`` and ``sync``, and recipe releases it once no installed pack
uses the URL.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

from untaped.sdk import Diagnostics, ErrorCategory, System, UntapedError, UsageError, attribution
from untaped_git.api import (
    RepoStore,
    check_names,
    default_branch,
    ls_remote,
    store_key,
)
from untaped_recipe.errors import PackFetchError

_GIT_URL_PREFIXES = ("https://", "git@", "ssh://")
_COMMIT_ID = re.compile(r"[0-9a-f]{4,64}")  # matched lowercased
_EXECUTABLE = "100755"
_LINK = "120000"
_SUBMODULE = "160000"
_diag = Diagnostics()


def is_git_url(value: str) -> bool:
    """Return true when the CLI should treat ``value`` as a git URL."""
    return value.startswith(_GIT_URL_PREFIXES)


def fetch_pack_source(url: str, *, rev: str | None, dest: Path, plugin: object) -> str:
    """Fetch ``url`` at ``rev`` (its default branch when ``None``) and write its tree into ``dest``.

    Returns the commit written. ``rev`` is a branch, a tag (a branch wins, as
    with ``git clone --branch``) or a commit id recipe's fetched branches or
    tags reach. ``plugin`` names recipe, as for any store consumer.
    """
    if rev is not None and (not rev.strip() or rev.startswith("-")):
        raise UsageError(f"invalid --rev: {rev!r}")
    store = RepoStore.for_url(url, plugin=plugin, error=PackFetchError)
    commit = _fetch(store, url, rev)
    _write_tree(store, commit, dest)
    return commit


def release_unused_sources(
    released: Iterable[str], installed: Iterable[str], *, plugin: object
) -> None:
    """Release the store repo of each git URL in ``released`` that no ``installed`` source uses.

    Best effort: the pack change already happened, so a failed release is a
    warning, and the next ``add`` of that URL fetches into the kept repo again.
    """
    kept = {store_key(source) for source in installed if is_git_url(source)}
    for url in dict.fromkeys(released):
        if not is_git_url(url) or store_key(url) in kept:
            continue
        try:
            store = RepoStore.for_url(url, plugin=plugin, error=PackFetchError)
            if store.has_part():
                store.release()
        except UntapedError as exc:
            _diag.warning(f"could not release the repo store's copy of {url}: {exc}")


def _fetch(store: RepoStore, url: str, rev: str | None) -> str:
    """Fetch what ``rev`` needs into recipe's namespace and return its commit."""
    if rev is None:
        branch = _remote(lambda: default_branch(url))
        if branch is None:
            raise PackFetchError(
                f"{url} names no default branch; pass --rev", category=ErrorCategory.NOT_FOUND
            )
        rev = branch
    if check_names([rev]) is None and "*" not in rev:
        listed = _remote(lambda: ls_remote(url, [f"refs/heads/{rev}", f"refs/tags/{rev}"]))
        for kind in ("heads", "tags"):
            commit = listed.get(f"refs/{kind}/{rev}")
            if commit is not None:
                if kind == "heads":
                    store.fetch(branches=[rev])
                else:
                    store.fetch(tags=[rev])
                store.write_private()
                return _present(store, commit, url, rev)
    prefix = rev.lower()
    if not _COMMIT_ID.fullmatch(prefix):
        raise _not_found(url, rev)
    store.fetch(branches=["*"], tags=["*"], prune=True)
    store.write_private()
    found = _commits(store, prefix)
    if len(found) > 1:
        raise PackFetchError(
            f"{rev!r} names more than one commit of {url}; pass a longer id",
            category=ErrorCategory.CONFLICT,
        )
    if not found:
        raise _not_found(url, rev)
    return found[0]


def _commits(store: RepoStore, prefix: str) -> list[str]:
    """The commits whose id starts with ``prefix`` that recipe's branches or tags reach.

    Objects are listed by id (``--disambiguate``), never by ref name, so a ref
    another plugin or a person named like the id resolves nothing.
    """
    listed = store.run(["rev-parse", f"--disambiguate={prefix}"], capture=True, check=False)
    commits = set()
    for oid in listed.text.split():
        peeled = store.run(
            ["rev-parse", "--verify", "--quiet", f"{oid}^{{commit}}"], capture=True, check=False
        )
        if peeled.returncode == 0:
            commits.add(peeled.text.strip())
    return sorted(commit for commit in commits if _reached(store, commit))


def _present(store: RepoStore, commit: str, url: str, rev: str) -> str:
    """``commit``, the remote's listing of ``rev``, once the fetch brought it in."""
    found = store.run(
        ["rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"], capture=True, check=False
    )
    if found.returncode != 0:
        raise PackFetchError(
            f"{rev!r} of {url} moved while it was fetched; run the command again",
            category=ErrorCategory.CONFLICT,
        )
    return commit


def _reached(store: RepoStore, commit: str) -> bool:
    """Whether one of recipe's branches or tags contains ``commit``."""
    found = store.run(
        ["for-each-ref", "--count=1", "--format=%(refname)", f"--contains={commit}", *store.roots],
        capture=True,
    )
    return bool(found.text.strip())


def _write_tree(store: RepoStore, commit: str, dest: Path) -> None:
    """Write ``commit``'s files into ``dest`` as committed: modes kept, links refused."""
    entries = store.ls_tree(commit)
    for entry in entries:
        _check_path(entry.path)
        if entry.mode == _LINK:
            raise ValueError(f"pack must not contain symlinks: {entry.path}")
    blobs = [entry for entry in entries if entry.mode != _SUBMODULE]
    contents = _read_blobs(store, commit, [entry.oid for entry in blobs])
    try:
        dest.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            target = dest.joinpath(*PurePosixPath(entry.path).parts)
            if entry.mode == _SUBMODULE:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents[entry.oid])
            if entry.mode == _EXECUTABLE:
                target.chmod(0o755)
    except OSError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise PackFetchError(
            f"could not write the pack into {dest}: {exc}",
            category=ErrorCategory.FAILED,
            system=System.LOCAL,
        ) from exc


def _read_blobs(store: RepoStore, commit: str, oids: list[str]) -> dict[str, bytes]:
    """``oid → content`` of ``commit``'s blobs ``oids``, prefetched in one round trip."""
    if not oids:
        return {}
    unique = list(dict.fromkeys(oids))
    handle = store.prefetched(trees=[commit])
    out = handle.run(["cat-file", "--batch"], stdin="".join(f"{oid}\n" for oid in unique)).stdout
    contents: dict[str, bytes] = {}
    offset = 0
    for oid in unique:
        end = out.find(b"\n", offset)
        header = out[offset:end].decode("ascii", errors="replace").split(" ") if end >= 0 else []
        size = int(header[2]) if len(header) == 3 and header[2].isdigit() else -1
        start = end + 1
        if header[:2] != [oid, "blob"] or size < 0 or out[start + size : start + size + 1] != b"\n":
            raise PackFetchError(
                f"the repo store could not read {oid} of {commit}; run the command again",
                category=ErrorCategory.FAILED,
                system=System.GIT,
            )
        contents[oid] = out[start : start + size]
        offset = start + size + 1
    return contents


def _check_path(path: str) -> None:
    """Refuse a tree path that would write outside the pack or into a ``.git``."""
    parts = PurePosixPath(path).parts
    if (
        PurePosixPath(path).is_absolute()
        or not parts
        or any(part in ("..", ".") or part.lower() == ".git" for part in parts)
    ):
        raise ValueError(f"pack contains an unsafe path: {path!r}")


def _remote[T](query: Callable[[], T]) -> T:
    """Run a remote query, reporting git's failure as recipe's fetch error."""
    try:
        return query()
    except UntapedError as exc:
        raise PackFetchError(str(exc), hint=exc.hint, **attribution(exc)) from exc


def _not_found(url: str, rev: str) -> PackFetchError:
    return PackFetchError(
        f"{url} has no branch, tag or commit {rev!r}", category=ErrorCategory.NOT_FOUND
    )
