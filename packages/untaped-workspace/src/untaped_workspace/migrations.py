"""Workspace's ``setup migrate-dirs`` rows: its 10.x clones into the repo store.

Like ``cli``, this module is a composition root: it reads ``state.yml`` and
the invocation's profile and wires the store and the worktrees together.

``workspace.cache`` moves every repository of the 10.x cache
(``~/.untaped/workspace-cache``, or a custom root a deleted
``workspace.cache_dir`` still names in config.yml) into the git plugin's repo
store with ``untaped_git.api.adopt``. A 10.x repository is a full clone in
the plain-clone layout workspace keeps, so nothing is renamed or downloaded:
its worktrees are repointed, each one ``state.yml`` lists is stamped as
workspace's and gets its ``config.worktree`` (refspec, URL, credential
helper), the shared ``remote.origin.fetch`` goes, and ``untaped-workspace.json``
records a ``partial`` history. A 9.x mirror in that root (unmarked, holding
``refs/heads``) is deleted, never adopted.

``workspace.repositories`` is the 9.x ``~/.untaped/repositories``, which
clones made before 7.0 may borrow objects from (``objects/info/alternates``).
Borrowers are found under ``workspaces_dir`` and at every path ``state.yml``
lists; one elsewhere on disk can't be, so the directory stays unless
``--dissociate`` is given, which repacks each borrower found (``git repack -a
-d``, what ``clone --dissociate`` does) and deletes it. A 9.x mirror a
borrower uses is kept the same way.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from untaped.sdk import (
    GitCommandError,
    MigrationOptions,
    MigrationOutcome,
    MigrationRow,
    PluginContext,
    UntapedError,
    app_context,
    cache_origin,
    dir_bytes,
    old_dirs,
    plural,
    run_git,
    shown_path,
    size_text,
    unsafe_dir,
)
from untaped_workspace.application.locate import workspace_root
from untaped_workspace.errors import GitError
from untaped_workspace.settings import WorkspaceSettings

#: Where 10.x kept workspace's clones unless ``workspace.cache_dir`` said otherwise.
DEFAULT_ROOT = "~/.untaped/workspace-cache"
#: The 9.x cache, which clones made before 7.0 may borrow objects from.
REPOSITORIES = "~/.untaped/repositories"
_TIMEOUT = 60.0
_REPACK_TIMEOUT = 1800.0
_CACHE_ID = "workspace.cache"
_REPOSITORIES_ID = "workspace.repositories"


@dataclass(frozen=True)
class _Old:
    """One repository of a 10.x root: adopted, or (``mirror``) a 9.x mirror to delete."""

    path: Path
    mirror: bool


def repositories() -> Path:
    return Path(REPOSITORIES).expanduser()


def roots() -> list[Path]:
    """The 10.x roots: the default and any custom ``workspace.cache_dir`` still configured.

    A root naming the 9.x ``repositories`` is that directory's row, not this one.
    """
    lender = _real(repositories())
    return [
        root for root in old_dirs(DEFAULT_ROOT, "workspace", "cache_dir") if _real(root) != lender
    ]


# -- workspace.cache ------------------------------------------------------


def preview_cache(ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationRow]:
    rows: list[MigrationRow] = []
    listed = _listed_worktrees(ctx)
    scanned: dict[Path, list[_Old]] = {}
    for root in roots():
        if root.is_dir() and (reason := _refused(root)) is not None:
            rows.append(MigrationRow(action="keep", source=str(root), detail=f"kept: {reason}"))
        else:
            scanned[root] = _scan(root)
    borrowed = _borrowers(ctx, _mirrors(scanned))
    for root, found in scanned.items():
        adopted = [repo.path for repo in found if not repo.mirror]
        if adopted:
            stamped = sum(len(_worktrees_of(repo, listed)) for repo in adopted)
            overlap = sum(_in_store(repo) for repo in adopted)
            detail = (
                f"{plural(len(adopted), 'repo')}, kept as they are (full clones); "
                f"config.worktree for {plural(stamped, 'workspace worktree')}, the shared "
                "remote.origin.fetch removed"
            )
            if overlap:
                detail += (
                    f"; {plural(overlap, 'repo')} already in the store: the copy holding work "
                    "is kept"
                )
            rows.append(
                MigrationRow(
                    action="move",
                    source=str(root),
                    destination=str(_store_root()),
                    detail=detail,
                    bytes=sum(_size(repo, options) for repo in adopted),
                )
            )
        for repo in found:
            if not repo.mirror:
                continue
            users = _users(borrowed, repo.path)
            if users and not options.dissociate:
                rows.append(_kept(repo.path, users, options))
                continue
            detail = "9.x mirror, never adopted"
            if users:
                detail += f"; {_repacks(users)} first"
            rows.append(
                MigrationRow(
                    action="delete",
                    source=str(repo.path),
                    detail=detail,
                    bytes=_size(repo.path, options),
                )
            )
    return rows


def apply_cache(ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationOutcome]:
    from untaped_git.api import remove_if_emptied  # noqa: PLC0415

    move = _mover(_listed_worktrees(ctx))
    done = {"moved": 0, "deleted": 0, "kept": 0}
    failures: list[str] = []
    left: list[str] = []
    scanned = {
        _real(root): _scan(root) for root in roots() if root.is_dir() and _refused(root) is None
    }
    borrowed = _borrowers(ctx, _mirrors(scanned))
    for root, found in scanned.items():
        for repo in found:
            try:
                if repo.mirror:
                    done[_drop_mirror(repo.path, _users(borrowed, repo.path), options)] += 1
                else:
                    move(repo.path)
                    done["moved"] += 1
            except (UntapedError, OSError) as exc:
                failures.append(f"{shown_path(repo.path)}: {_reason(exc)}")
        if not remove_if_emptied(root):
            left.append(shown_path(root))
    _drop_dangling_links()
    if not scanned:
        detail = "no 10.x workspace cache"
        return [MigrationOutcome(id=_CACHE_ID, action="unchanged", detail=detail)]
    parts = [f"moved {plural(done['moved'], 'repo')} into the repo store"]
    if done["deleted"]:
        parts.append(f"deleted {plural(done['deleted'], '9.x mirror')}")
    if done["kept"]:
        parts.append(f"kept {plural(done['kept'], '9.x mirror')} clones borrow from (--dissociate)")
    parts += failures
    if left:
        parts.append(f"kept {', '.join(left)}: something other than repositories is left there")
    changed = done["moved"] or done["deleted"]
    action = "moved" if not failures else ("partial" if changed else "failed")
    return [MigrationOutcome(id=_CACHE_ID, action=action, detail="; ".join(parts))]


def _mover(listed: Sequence[Path]) -> Callable[[Path], None]:
    """Move one 10.x repository into the store, then give workspace's worktrees their config."""
    from untaped_git.api import adopt  # noqa: PLC0415
    from untaped_workspace import SPEC  # noqa: PLC0415
    from untaped_workspace.infrastructure.git_worktrees import LocalGitWorktrees  # noqa: PLC0415

    worktrees = LocalGitWorktrees(profile=app_context().profile)

    def move(repo: Path) -> None:
        label = cache_origin(repo)
        owned = _worktrees_of(repo, listed)
        adopt(repo, plugin=SPEC, error=GitError, owned=owned)
        if label is not None:
            worktrees.adopted(label, owned)

    return move


def _drop_mirror(repo: Path, users: Sequence[Path], options: MigrationOptions) -> str:
    """Delete a 9.x mirror (repacking its borrowers with ``--dissociate``); ``deleted``/``kept``."""
    if users and not options.dissociate:
        return "kept"
    _dissociate(users)
    shutil.rmtree(repo)
    return "deleted"


def _drop_dangling_links() -> None:
    for root in roots():
        if root.is_symlink() and not root.exists():
            root.unlink()  # its directory was emptied and removed


def _scan(root: Path) -> list[_Old]:
    from untaped_git.api import bare_repos  # noqa: PLC0415

    return [_Old(repo, _is_mirror(repo)) for repo in bare_repos(root)]


def _mirrors(scanned: dict[Path, list[_Old]]) -> list[Path]:
    return [repo.path for found in scanned.values() for repo in found if repo.mirror]


def _is_mirror(repo: Path) -> bool:
    """Unmarked and holding ``refs/heads``: a 9.x mirror (10.x's own rule).

    A repo marked by 10.x (``untaped.layout``) or by the store (``untaped.store``) is not.
    """
    marks = _git(repo, ["config", "--get-regexp", r"^untaped\.(layout|store)$"], check=False)
    if marks.strip():
        return False
    return bool(
        _git(repo, ["for-each-ref", "--count=1", "--format=%(refname)", "refs/heads"]).strip()
    )


def _in_store(repo: Path) -> bool:
    from untaped_git.api import RepoStore  # noqa: PLC0415
    from untaped_workspace import SPEC  # noqa: PLC0415

    label = cache_origin(repo)
    if label is None:
        return False
    return RepoStore.for_url(label, plugin=SPEC, error=GitError).exists()


def _listed_worktrees(ctx: PluginContext) -> list[Path]:
    """Every worktree directory ``state.yml``'s active workspaces list."""
    from untaped_workspace.infrastructure.state_store import StateWorkspaceStore  # noqa: PLC0415

    root = _workspaces_dir(ctx)
    return [
        workspace_root(root, record.name) / spec.dir
        for record in StateWorkspaceStore().active()
        for spec in record.repos
    ]


def _worktrees_of(repo: Path, listed: Iterable[Path]) -> list[Path]:
    """The listed worktrees whose ``.git`` file points into ``repo``."""
    admin = _real(repo / "worktrees")
    found = []
    for path in listed:
        try:
            text = (path / ".git").read_text(encoding="utf-8")
        except OSError:
            continue
        gitdir = text.strip().removeprefix("gitdir:").strip()
        if gitdir and _real(path / gitdir).is_relative_to(admin):
            found.append(path)
    return found


# -- workspace.repositories -----------------------------------------------


def preview_repositories(ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationRow]:
    lender = repositories()
    if not lender.is_dir():
        return []
    users = _users(_borrowers(ctx, [lender]), lender)
    if not options.dissociate:
        return [_kept(lender, users, options)]
    detail = "9.x cache" + (f"; {_repacks(users)} first" if users else "")
    size = _size(lender, options)
    return [MigrationRow(action="delete", source=str(lender), detail=detail, bytes=size)]


def apply_repositories(ctx: PluginContext, options: MigrationOptions) -> Sequence[MigrationOutcome]:
    lender = repositories()
    if not lender.is_dir():
        return [MigrationOutcome(id=_REPOSITORIES_ID, action="unchanged", detail="no 9.x cache")]
    if not options.dissociate:
        detail = f"kept {shown_path(lender)}: --dissociate repacks its borrowers and deletes it"
        return [MigrationOutcome(id=_REPOSITORIES_ID, action="unchanged", detail=detail)]
    users = _users(_borrowers(ctx, [lender]), lender)
    size = dir_bytes(lender)
    try:
        _dissociate(users)
        shutil.rmtree(lender)
    except (UntapedError, OSError) as exc:
        detail = f"kept {shown_path(lender)}: {_reason(exc)}"
        return [MigrationOutcome(id=_REPOSITORIES_ID, action="failed", detail=detail)]
    detail = f"deleted {shown_path(lender)} ({size_text(size)} freed)"
    if users:
        detail += f" after repacking {plural(len(users), 'clone')}"
    return [MigrationOutcome(id=_REPOSITORIES_ID, action="deleted", detail=detail)]


# -- borrowers ------------------------------------------------------------


def _borrowers(ctx: PluginContext, lenders: Sequence[Path]) -> dict[Path, set[Path]]:
    """Each clone found that borrows objects from one of ``lenders`` → the lenders it uses.

    Clones are looked for one and two levels under ``workspaces_dir`` and at
    every worktree path ``state.yml`` lists, active and archived.
    """
    if not lenders:
        return {}
    real = {_real(lender): lender for lender in lenders}
    found: dict[Path, set[Path]] = {}
    for clone in _candidates(ctx):
        for target in _alternates(clone):
            for root, lender in real.items():
                if target.is_relative_to(root):
                    found.setdefault(clone, set()).add(lender)
    return found


def _candidates(ctx: PluginContext) -> list[Path]:
    from untaped_workspace.infrastructure.state_store import StateWorkspaceStore  # noqa: PLC0415

    root = _workspaces_dir(ctx)
    seen: list[Path] = []
    for path in [*_children(root), *(child for top in _children(root) for child in _children(top))]:
        if path not in seen:
            seen.append(path)
    store = StateWorkspaceStore()
    for record in [*store.active(), *store.archived()]:
        for spec in record.repos:
            path = workspace_root(root, record.name) / spec.dir
            if path not in seen:
                seen.append(path)
    return seen


def _children(directory: Path) -> list[Path]:
    try:
        return sorted(
            path
            for path in directory.expanduser().iterdir()
            if path.is_dir() and not path.is_symlink()
        )
    except OSError:
        return []


def _alternates(clone: Path) -> list[Path]:
    """The object directories ``clone``'s ``.git/objects/info/alternates`` names."""
    objects = clone / ".git" / "objects"
    try:
        text = (objects / "info" / "alternates").read_text(encoding="utf-8")
    except OSError:
        return []
    paths = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            paths.append(_real(objects / line))
    return paths


def _users(borrowed: dict[Path, set[Path]], lender: Path) -> list[Path]:
    return sorted(clone for clone, lenders in borrowed.items() if lender in lenders)


def _dissociate(clones: Sequence[Path]) -> None:
    """Copy every borrowed object into each clone, then drop its ``alternates``."""
    for clone in clones:
        _git(clone / ".git", ["repack", "-a", "-d", "-q"], cwd=clone, timeout=_REPACK_TIMEOUT)
        (clone / ".git" / "objects" / "info" / "alternates").unlink(missing_ok=True)


def _kept(lender: Path, users: Sequence[Path], options: MigrationOptions) -> MigrationRow:
    if users:
        detail = (
            f"{plural(len(users), 'clone')} ({', '.join(shown_path(user) for user in users)}) "
            "borrow from it (objects/info/alternates); --dissociate repacks them and deletes it"
        )
    else:
        detail = (
            "no clone under workspaces_dir or in state.yml borrows from it, but one elsewhere "
            "on disk can't be found; --dissociate deletes it"
        )
    return MigrationRow(
        action="keep", source=str(lender), detail=detail, bytes=_size(lender, options)
    )


def _repacks(users: Sequence[Path]) -> str:
    return f"repacks {plural(len(users), 'clone')} that borrow from it"


# -- plumbing -------------------------------------------------------------


def _refused(root: Path) -> str | None:
    """Why ``root`` is never moved whole, or ``None``."""
    from untaped_git.api import overlaps_store  # noqa: PLC0415

    if overlaps_store(root):
        return (
            f"it overlaps the repo store ({shown_path(_store_root())}): set git.store_dir elsewhere"
        )
    return unsafe_dir(root)


def _store_root() -> Path:
    from untaped_git.api import store_root  # noqa: PLC0415

    return store_root()


def _size(path: Path, options: MigrationOptions) -> int:
    return dir_bytes(path) if options.measure else 0


def _workspaces_dir(ctx: PluginContext) -> Path:
    settings = ctx.settings if isinstance(ctx.settings, WorkspaceSettings) else WorkspaceSettings()
    return settings.workspaces_dir.expanduser()


def _git(
    git_dir: Path,
    argv: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: float = _TIMEOUT,
) -> str:
    try:
        return run_git(
            argv,
            git_dir=git_dir,
            cwd=cwd or git_dir,
            timeout=timeout,
            capture=True,
            check=check,
            ceiling=True,
            batch_ssh=False,
        ).text
    except GitCommandError as exc:
        raise GitError(str(exc)) from exc


def _reason(exc: Exception) -> str:
    if isinstance(exc, OSError):
        return str(exc.strerror or exc)
    return str(exc)


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path.expanduser()))
