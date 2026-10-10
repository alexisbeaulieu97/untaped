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
sync`` fetches its history once. (``github.corpus``, the 9.x corpus's
deletion, is a ``delete_migration`` in the plugin's spec.)
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from untaped.sdk import (
    MigrationOutcome,
    MigrationRow,
    cache_origin,
    dir_bytes,
    old_dirs,
    plugin_dir,
    plural,
)
from untaped_git.api import RepoStore, adopt, bare_repos, remove_if_emptied
from untaped_github import SPEC
from untaped_github.errors import GitCorpusError

#: Where 10.x kept the sweep cache unless ``github.cache_dir`` said otherwise.
DEFAULT_ROOT = "~/.untaped/github-cache"
_OLD_METADATA = "untaped-corpus.json"
_WORKTREES = "worktrees"
_ID = "github.cache"


def roots() -> list[Path]:
    """The 10.x cache roots: the default and any custom ``github.cache_dir`` still configured."""
    return old_dirs(DEFAULT_ROOT, "github", "cache_dir")


def preview_cache() -> Sequence[MigrationRow]:
    rows: list[MigrationRow] = []
    shallow = 0
    for root in roots():
        repos = bare_repos(root, skip=(_WORKTREES,))
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
                destination="the repo store",
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
                bytes=dir_bytes(root) - sum(dir_bytes(path) for path in worktrees),
            )
        )
        if worktrees:
            rows.append(
                MigrationRow(
                    action="move",
                    source=str(root / _WORKTREES),
                    destination=str(plugin_dir(SPEC) / _WORKTREES),
                    detail=f"{plural(len(worktrees), 'worktree')}, repaired, stamped as github's",
                    bytes=sum(dir_bytes(path) for path in worktrees),
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
        if not root.is_dir():
            continue
        seen = True
        for repo in bare_repos(root, skip=(_WORKTREES,)):
            _rename_metadata(repo)
            try:
                adopted = adopt(
                    repo,
                    plugin=SPEC,
                    error=GitCorpusError,
                    worktrees=(root / _WORKTREES, plugin_dir(SPEC) / _WORKTREES),
                )
            except GitCorpusError as exc:
                failures.append(f"{repo}: {exc}")
                continue
            if adopted.action == "dropped":
                dropped += 1
            else:
                moved += 1
        if not remove_if_emptied(root):
            left.append(str(root))
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
