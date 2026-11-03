"""How the store fetches a plugin's refs: :meth:`RepoStore.fetch` and the remote's default branch.

Every store fetch passes ``--no-tags`` (tags move only by the explicit
refspecs the store builds), ``--no-write-fetch-head``, ``--filter=blob:none``
and ``--prune`` or ``--no-prune`` explicitly, so a user's global
``fetch.prune`` never decides; it names the repo with ``--git-dir`` and never
runs from a worktree. ``remote.origin.url`` is written once at creation as a
label and never repointed: a fetch of another spelling of the URL rewrites
the label to it with a command-scope ``url.<url>.insteadOf``, and the
credentials (from :class:`~untaped_git.hosts.GitHost`) are scoped to the URL
being fetched (:meth:`~untaped_git.store_git.StoreGit.network`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import ErrorCategory, UntapedError
from untaped.sdk.storage import NO_PROGRESS, ProgressHandle
from untaped_git.delta import RefDelta, diff_refs
from untaped_git.namespace import ORIGIN_HEAD, check_names, covered, is_glob
from untaped_git.repo_lock import repo_lock
from untaped_git.run import run_git
from untaped_git.store_git import SLOW_TIMEOUT, TIMEOUT

if TYPE_CHECKING:
    from untaped_git.store_git import StoreGit

#: Explicit refs fetched per command: a dropped connection loses one batch.
DEFAULT_FETCH_BATCH_SIZE = 50
_FILTER_IGNORED = "filtering not recognized by server"


def fetch(
    store: StoreGit,
    *,
    branches: Sequence[str] = (),
    tags: Sequence[str] = (),
    prune: bool = False,
    progress: ProgressHandle = NO_PROGRESS,
) -> RefDelta:
    """Fetch ``branches`` and ``tags`` into the plugin's refs, as :meth:`RepoStore.fetch` says."""
    reason = check_names([*branches, *tags])
    if reason is not None:
        raise store.error(reason, category=ErrorCategory.INVALID)
    if not branches and not tags:
        return RefDelta()
    with store.locked():
        store.ensure()
        store.remove_stale_leftovers()
        before = store.namespace()
        if all(is_glob(name) for name in (*branches, *tags)):
            progress.update(phase="fetching refs")  # one git call: no counts
            stderr = _fetch_globs(store, branches, tags, prune=prune)
        else:
            stderr = _fetch_listed(
                store, branches, tags, prune=prune, before=before, progress=progress
            )
        if store.layout.plain:
            _write_origin_head(store, branches, tags)
        after = store.namespace()
        delta = diff_refs(before, after)
        _record_filter(store, stderr, delta)
        store.maintain()
    return delta


def _fetch_globs(
    store: StoreGit, branches: Sequence[str], tags: Sequence[str], *, prune: bool
) -> str:
    layout = store.layout
    heads = layout.refspecs("heads", branches)
    tag_specs = layout.refspecs("tags", tags)
    stderr: list[str] = []
    if layout.prune_tags:
        # One command: --prune applies to every refspec on it, all prunable.
        stderr.append(_fetch_refspecs(store, [*heads, *tag_specs], prune=prune))
    else:
        # --prune never shares a command line with a refspec it must not prune.
        if heads:
            stderr.append(_fetch_refspecs(store, heads, prune=prune))
        if tag_specs:
            stderr.append(_fetch_refspecs(store, tag_specs, prune=False))
    if prune:
        store.delete(
            layout.absolute(ref)
            for ref in store.namespace()
            if layout.prunable(ref) and not covered(ref, branches, tags)
        )
    return "".join(stderr)


def _fetch_listed(
    store: StoreGit,
    branches: Sequence[str],
    tags: Sequence[str],
    *,
    prune: bool,
    before: Mapping[str, str],
    progress: ProgressHandle,
) -> str:
    layout = store.layout
    progress.update(phase="listing refs")
    remote = _list_remote(store, heads=bool(branches), tags=bool(tags))
    wanted = [
        ref
        for ref, oid in remote.items()
        if covered(ref, branches, tags) and before.get(ref) != oid
    ]
    stderr: list[str] = []
    progress.update(phase="fetching refs", done=0, total=len(wanted))
    for start in range(0, len(wanted), DEFAULT_FETCH_BATCH_SIZE):
        batch = wanted[start : start + DEFAULT_FETCH_BATCH_SIZE]
        specs = []
        for ref in batch:
            kind, _, name = ref.partition("/")
            specs.extend(layout.refspecs(kind, [name]))
        stderr.append(_fetch_refspecs(store, specs, prune=False))
        progress.update(phase="fetching refs", done=start + len(batch), total=len(wanted))
    if prune:
        store.delete(
            layout.absolute(ref)
            for ref in store.namespace()
            if layout.prunable(ref) and (not covered(ref, branches, tags) or ref not in remote)
        )
    return "".join(stderr)


def _list_remote(store: StoreGit, *, heads: bool, tags: bool) -> dict[str, str]:
    patterns = [*(["refs/heads/*"] if heads else []), *(["refs/tags/*"] if tags else [])]
    result = store.network(["ls-remote", "--refs", "origin", *patterns], capture=True)
    listed: dict[str, str] = {}
    for line in result.text.splitlines():
        oid, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            listed[f"heads/{ref.removeprefix('refs/heads/')}"] = oid
        elif ref.startswith("refs/tags/"):
            listed[f"tags/{ref.removeprefix('refs/tags/')}"] = oid
    return listed


def _fetch_refspecs(store: StoreGit, refspecs: Sequence[str], *, prune: bool) -> str:
    if not refspecs:
        return ""
    argv = [
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        "--recurse-submodules=no",
        "--filter=blob:none",
        "--prune" if prune else "--no-prune",
        # Checked per command: one unshallowing fetch removes the file.
        *(["--unshallow"] if (store.path / "shallow").exists() else []),
        "origin",
        *refspecs,
    ]
    return store.network(argv, timeout=SLOW_TIMEOUT, retry=True).stderr


def _write_origin_head(store: StoreGit, branches: Sequence[str], tags: Sequence[str]) -> None:
    """Point ``refs/remotes/origin/HEAD`` (plain-clone layout) at the default branch.

    The branch is the recorded ``untaped.defaultBranch`` (``default_branch()``
    records it); with none recorded, or one whose ref a prune removed
    (the remote renamed it), the remote is asked once and the answer recorded.
    A recorded branch this call didn't name is left alone: its ref is simply
    not fetched yet.
    """
    symref = ["symbolic-ref", "--quiet", ORIGIN_HEAD]
    target = store.git(symref, capture=True, check=False).text.strip()
    if target and store.has_ref(target):
        return
    branch = store.config_get("untaped.defaultBranch")
    if branch is None or (
        covered(f"heads/{branch}", branches, tags)
        and not store.has_ref(f"refs/remotes/origin/{branch}")
    ):
        branch = _remote_default_branch(store)
    ref = f"refs/remotes/origin/{branch}"
    if branch is not None and store.has_ref(ref):
        store.git(["symbolic-ref", ORIGIN_HEAD, ref])


def _remote_default_branch(store: StoreGit) -> str | None:
    result = store.network(["ls-remote", "--symref", "origin", "HEAD"], capture=True)
    branch = parse_symref(result.text)
    if branch is not None:
        record_default_branch(store.path, branch)
    return branch


def _record_filter(store: StoreGit, stderr: str, delta: RefDelta) -> None:
    """Record whether the host honoured ``--filter`` (``untaped.filter``).

    Only a fetch that brought something says: the warning, confirmed by
    the new tips' trees holding every blob, means ``ignored``.
    """
    tips = [*delta.added.values(), *(move.new for move in delta.moved.values())]
    if not tips:
        return
    value = "honoured"
    if _FILTER_IGNORED in stderr.lower() and not store.missing([f"{tip}^{{tree}}" for tip in tips]):
        value = "ignored"
    if store.config_get("untaped.filter") != value:
        store.git(["config", "untaped.filter", value])


def record_default_branch(repo: Path, branch: str) -> None:
    """Record ``branch`` as ``repo``'s remote default (``untaped.defaultBranch``) if it exists.

    Best effort, under the repo's lock: a busy or unwritable repo only means
    its next fetch asks the remote itself.
    """
    if not (repo / "HEAD").is_file():
        return
    try:
        # Try once: when another writer holds the repo, its next fetch asks the remote itself.
        with repo_lock(repo, timeout=0, error=UntapedError):
            run_git(
                ["config", "--file", str(repo / "config"), "untaped.defaultBranch", branch],
                cwd=repo,
                timeout=TIMEOUT,
                ceiling=True,
                batch_ssh=False,
            )
    except UntapedError:  # GitCommandError included
        return


def parse_symref(text: str) -> str | None:
    """The branch of ``ref: refs/heads/<b>\\tHEAD`` in ``ls-remote --symref`` output."""
    for line in text.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line.removeprefix("ref: refs/heads/").removesuffix("\tHEAD")
    return None
