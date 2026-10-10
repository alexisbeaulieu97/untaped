"""GitHub's ``setup migrate-dirs`` rows: its 10.x sweep cache into the repo store.

``github.cache`` moves every repository of the 10.x cache
(``~/.untaped/github-cache``, or a custom root a deleted ``github.cache_dir``
still names in config.yml) into the git plugin's repo store with
``untaped_git.api.adopt``: its ``refs/heads/*`` and ``refs/tags/*`` become
``refs/untaped/github/*``, ``untaped-corpus.json`` becomes
``untaped-github.json``, and the sweep's worktrees move from ``<root>/worktrees``
to ``~/.untaped/plugins/github/worktrees/``, stamped as github's (no refspec).
A repository the store already holds is settled by the store's overlap rule.
A 10.x repository is shallow; it stays readable and the first ``github cache
sync`` fetches its history once. A root that is, holds or lies inside the
repo store (or untaped's own directories) is a ``keep`` row and stays.
(``github.corpus``, the 9.x corpus's deletion, is a ``delete_migration`` in
the plugin's spec.)
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from untaped.sdk import (
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    UntapedError,
    cache_origin,
    dir_bytes,
    old_dirs,
    plugin_dir,
    plural,
    run_git,
    shown_path,
    unsafe_dir,
)
from untaped_git.api import (
    RepoStore,
    adopt,
    bare_repos,
    overlaps_store,
    remove_if_emptied,
    store_root,
)
from untaped_github import SPEC
from untaped_github.errors import GitCorpusError

#: Where 10.x kept the sweep cache unless ``github.cache_dir`` said otherwise.
DEFAULT_ROOT = "~/.untaped/github-cache"
_OLD_METADATA = "untaped-corpus.json"
_WORKTREES = "worktrees"
_ID = "github.cache"


def roots() -> list[Path]:
    """The 10.x cache roots: the default and any custom ``github.cache_dir`` still configured.

    ``github.corpus_path``, the name before 10.1, counts too.
    """
    found = old_dirs(DEFAULT_ROOT, "github", "cache_dir")
    return [
        *found,
        *(path for path in old_dirs(DEFAULT_ROOT, "github", "corpus_path") if path not in found),
    ]


def refused(root: Path) -> str | None:
    """Why ``root`` is never moved whole, or ``None``."""
    if overlaps_store(root):
        return (
            f"it overlaps the repo store ({shown_path(store_root())}): set git.store_dir elsewhere"
        )
    return unsafe_dir(root)


def preview_cache(options: MigrationOptions) -> Sequence[MigrationRow]:
    rows: list[MigrationRow] = []
    shallow = 0
    for root in roots():
        if root.is_dir() and (reason := refused(root)) is not None:
            rows.append(MigrationRow(action="keep", source=str(root), detail=f"kept: {reason}"))
            continue
        repos = _repos(root)
        worktrees = _worktrees(root)
        if not root.is_dir() or (not repos and not worktrees):
            continue
        overlap = 0
        for repo in repos:
            target = _target(repo)
            overlap += target is not None and (target / "HEAD").is_file()
            shallow += (repo / "shallow").is_file()
        rows.append(
            MigrationRow(
                action="move",
                source=str(root),
                destination=str(store_root()),
                detail=(
                    f"{plural(len(repos), 'repo')}; refs/heads/* and refs/tags/* renamed to "
                    "refs/untaped/github/*"
                    + (
                        f"; {plural(overlap, 'repo')} already in the store: the copy holding "
                        "work is kept, and github's next sync fetches into it"
                        if overlap
                        else ""
                    )
                ),
                bytes=_size(root, options) - sum(_size(path, options) for path in worktrees),
            )
        )
        if worktrees:
            rows.append(
                MigrationRow(
                    action="move",
                    source=str(root / _WORKTREES),
                    destination=str(plugin_dir(SPEC) / _WORKTREES),
                    detail=f"{plural(len(worktrees), 'worktree')}, repaired, stamped as github's",
                    bytes=sum(_size(path, options) for path in worktrees),
                )
            )
    if shallow:
        rows.append(
            MigrationRow(
                action="then",
                detail=(
                    f"the first `untaped github cache sync` downloads the history (commits and "
                    f"trees, not file contents) of {plural(shallow, 'shallow repo')} once"
                ),
            )
        )
    return rows


def apply_cache() -> Sequence[MigrationOutcome]:
    moved, dropped, failures = 0, 0, []
    left: list[str] = []
    seen = False
    for root in roots():
        if not root.is_dir() or refused(root) is not None:
            continue
        seen = True
        root = Path(os.path.realpath(root))  # a symlinked root migrates through its target
        for repo in _repos(root):
            try:
                _rename_metadata(repo)
                adopted = adopt(
                    repo,
                    plugin=SPEC,
                    error=GitCorpusError,
                    worktrees=(root / _WORKTREES, plugin_dir(SPEC) / _WORKTREES),
                )
            except (UntapedError, OSError) as exc:
                reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
                failures.append(f"{shown_path(repo)}: {reason}")
                continue
            if adopted.action == "dropped":
                dropped += 1
            else:
                moved += 1
        if not remove_if_emptied(root):
            left.append(shown_path(root))
    for root in roots():
        if root.is_symlink() and not root.exists():
            root.unlink()  # its directory was emptied and removed
    if not seen:
        return [MigrationOutcome(id=_ID, action="unchanged", detail="no 10.x sweep cache")]
    parts = [f"moved {plural(moved, 'repo')} into the repo store"]
    if dropped:
        parts.append(f"{plural(dropped, 'copy', 'copies')} the store already held dropped")
    parts += failures
    if left:
        parts.append(f"kept {', '.join(left)}: something other than repositories is left there")
    action = "moved" if not failures else ("partial" if moved or dropped else "failed")
    return [MigrationOutcome(id=_ID, action=action, detail="; ".join(parts))]


def _repos(root: Path) -> list[Path]:
    """The root's repositories but workspace's (in a root both used): its own row moves those."""
    return [repo for repo in bare_repos(root, skip=(_WORKTREES,)) if not _workspaces(repo)]


def _workspaces(repo: Path) -> bool:
    marked = run_git(
        ["config", "--file", str(repo / "config"), "--get", "untaped.layout"],
        timeout=30.0,
        capture=True,
        check=False,
    )
    return bool(marked.text.strip())


def _size(path: Path, options: MigrationOptions) -> int:
    return dir_bytes(path) if options.measure else 0


def _worktrees(root: Path) -> list[Path]:
    directory = root / _WORKTREES
    try:
        return sorted(path for path in directory.iterdir() if path.is_dir())
    except OSError:
        return []


def _target(repo: Path) -> Path | None:
    label = cache_origin(repo)
    if label is None:
        return None
    return RepoStore.for_url(label, plugin=SPEC, error=GitCorpusError).path


def _rename_metadata(repo: Path) -> None:
    """``untaped-corpus.json`` → ``untaped-github.json`` (kept when the new one exists)."""
    old, new = repo / _OLD_METADATA, repo / "untaped-github.json"
    if old.is_file():
        if new.exists():
            old.unlink()
        else:
            old.rename(new)
