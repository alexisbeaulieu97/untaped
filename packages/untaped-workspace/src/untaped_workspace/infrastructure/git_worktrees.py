"""Git worktrees over the repo store: one store repo per repo URL, many worktrees.

Each repo URL maps to a repo of the git plugin's store
(``untaped_git.api.RepoStore``), where workspace's refs are the plain-clone
layout: ``refs/remotes/origin/*`` for the remote's branches, ``refs/tags/*``
for its tags, ``refs/heads/*`` for workspace branches. Workspaces get ``git
worktree`` checkouts from it, each with its own config (owner, refspec, URL,
credential helper) written by the store. The store locks each repo itself,
per call; failures become :class:`GitError` keeping git's attribution, and a
branch or directory that is already in use becomes a ``conflict``.

The store repo is blobless: a checkout has its tip's blobs, and the history
backfill that follows (``prefetch(history=)``) brings the rest, so ``git
blame`` and ``git log -p`` run offline. The backfill is never a gate: when it
stops, the worktree is still usable, the private file
``untaped-workspace.json`` says ``{"history": "partial"}``, and the next
fetch runs it whole again; once ``complete``, a fetch backfills only the
ranges it moved.

The store repo is load-bearing: every worktree references its objects, so
it goes only through :meth:`LocalGitWorktrees.release`, when ``remove`` drops
the last workspace of a URL. The repo store lock is always the inner lock (see
:mod:`untaped_workspace.application.provision`).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from untaped.sdk import (
    GitCommandError,
    GitResult,
    UntapedError,
    atomic_write,
    attribution,
    run_git,
    size_text,
)
from untaped_workspace.domain.models import (
    Checkout,
    LocalBranch,
    RepoRelease,
    StoredRepo,
    StoreUse,
    WorktreeStatus,
)
from untaped_workspace.domain.safety import archive_blockers
from untaped_workspace.errors import GitError, WorkspaceError

if TYPE_CHECKING:
    from untaped_git.api import RefDelta, RepoStore

type History = Literal["partial", "complete"]

#: What the history backfill walks: every commit of the remote's branches and tags.
HISTORY = ("refs/remotes/origin/*", "refs/tags/*")
_STASH_MARKERS = ("refs/stash", "logs/refs/stash", "reftable")
"""Paths under a common git dir whose absence means no stash (reftable: cannot tell)."""
_BRANCH_IN_USE = ("already checked out", "used by worktree")
_BRANCH_IN_USE_HINT = (
    "the branch is checked out in another workspace: archive that workspace or pick another branch"
)
_EXISTS_HINT = "move the existing directory aside, or use another branch name"


class LocalGitWorktrees:
    """:class:`GitWorktrees` backed by the repo store (``git.store_dir``)."""

    def __init__(self, *, git: str = "git", timeout: float = 60.0) -> None:
        self._git = git
        self._timeout = timeout

    # -- public port -------------------------------------------------------

    def checkout(self, url: str, dest: Path, *, branch: str | None, base: str | None) -> Checkout:
        """Add a worktree at ``dest``: on ``branch``, or detached at the base when ``None``.

        The history backfill runs last; when it stops, the checkout still
        succeeds and :attr:`Checkout.backfill_error` says why.
        """
        store = self._store(url)
        target = dest.absolute()
        delta = store.fetch(branches=["*"], tags=["*"], prune=True)
        history = self._history(store)
        picked = base if base is not None else self._default_branch(store, url)
        refs = self._refs(store, branch, picked)
        if f"refs/remotes/origin/{picked}" not in refs:
            raise GitError(
                f"base branch '{picked}' not found on origin of {url}", category="not_found"
            )
        store.run(["worktree", "prune"])
        if branch is None:
            store.worktree_add(target, f"refs/remotes/origin/{picked}")
            checkout = Checkout(
                action="checked_out", base=picked, detail=f"read-only at origin/{picked}"
            )
        else:
            checkout = self._add_writable(store, target, branch, picked, refs)
            self._set_upstream(store, branch)
        store.write_worktree_config(target, profile=None)
        failure = self._backfill(store, delta, history)
        return checkout.model_copy(update={"backfill_error": failure})

    def status(self, dest: Path, *, branch: str | None) -> WorktreeStatus | None:
        """Git state of the worktree at ``dest``; ``None`` when it is missing."""
        if not dest.exists():
            return None
        common = self._check_registered(dest)
        out = self._in(dest, ["status", "--porcelain=v2", "--branch"], capture=True).text
        head, upstream, ahead, behind, modified, untracked = _parse_status(out)
        if upstream is not None and not self._has_ref(dest, f"refs/remotes/{upstream}"):
            upstream = None  # configured for the first push, but not on origin yet
        return WorktreeStatus(
            branch=head,
            upstream=upstream,
            ahead=ahead,
            behind=behind,
            modified=modified,
            untracked=untracked,
            stashed=self._stashed(dest, branch, common=common),
            unpushed=self._unpushed(dest),
            submodules=(dest / ".gitmodules").exists() and self._has_submodules(dest),
        )

    def fetch(self, url: str, dest: Path) -> str | None:
        """Fetch ``url``'s store repo, rewrite ``dest``'s config and backfill what moved.

        A no-op when the store has no repo for ``url``. Returns why the
        history backfill stopped (a warning: the fetch itself succeeded), else
        ``None``.
        """
        store = self._store(url)
        if not store.exists():
            return None
        delta = store.fetch(branches=["*"], tags=["*"], prune=True)
        history = self._history(store)
        if dest.is_dir():
            store.write_worktree_config(dest.absolute(), profile=None)
        return self._backfill(store, delta, history)

    def in_store(self, url: str) -> bool:
        """Whether the repo store holds ``url``'s repo."""
        return self._store(url).exists()

    def remote_branches(self, url: str) -> list[str]:
        """Branch names of ``url``'s store repo as last fetched, sorted; ``[]`` when not stored."""
        refs = self._store(url).refs()
        return sorted(ref.removeprefix("heads/") for ref in refs if ref.startswith("heads/"))

    def stored_repos(self) -> list[StoredRepo]:
        """Every store repo holding ``untaped-workspace.json``, sorted by :attr:`StoredRepo.ident`.

        Read from disk alone (the repo picker opens on it). A repo whose
        recorded URL keys elsewhere, or that has no host (a local path), is
        left out: picking it would fill another store repo.
        """
        from untaped_git.api import RepoStore, store_key  # noqa: PLC0415
        from untaped_workspace import SPEC  # noqa: PLC0415

        found: list[StoredRepo] = []
        for store in RepoStore.owned_by(SPEC, error=GitError, map_error=_git_error):
            key = store_key(store.url)
            if key[0] != "_unknown" and store.path.parts[-len(key) :] == key:
                found.append(StoredRepo(key=key, origin=store.url))
        return sorted(found, key=lambda repo: repo.ident)

    def remove(self, url: str, dest: Path, *, force: bool) -> None:
        """Remove the worktree at ``dest`` and prune stale worktree entries."""
        store = self._store(url)
        if not store.exists():
            if not force:
                raise GitError(
                    f"the repo store has no repo for {url}",
                    category="failed",
                    hint="pass --force to delete the directory",
                )
            _delete_tree(dest)
            return
        if dest.exists():
            if not force:
                self._recheck(store, dest)
            self._remove_worktree(store, dest, force=force)
        store.run(["worktree", "prune"])

    def store_use(self, url: str) -> StoreUse | None:
        """Local branches and workspace worktrees of ``url``'s store repo; ``None``: not stored."""
        store = self._store(url)
        if not store.exists():
            return None
        listed = store.run(["worktree", "list", "--porcelain"], capture=True).text
        checked_out: set[str] = set()
        owned = 0
        for block in listed.split("\n\n"):
            lines = block.splitlines()
            if not lines or "bare" in lines:
                continue
            checked_out.update(
                line.removeprefix("branch refs/heads/")
                for line in lines
                if line.startswith("branch refs/heads/")
            )
            path = Path(lines[0].removeprefix("worktree "))
            if path.is_dir() and self._owner(path) == "workspace":
                owned += 1
        stashed = self._stash_branches(store)
        names = store.run(
            ["for-each-ref", "--format=%(refname:lstrip=2)", "refs/heads/"], capture=True
        ).text.split()
        branches = tuple(
            LocalBranch(
                name=name,
                checked_out=name in checked_out,
                unpushed=self._unpushed_on(store, name),
                stashed=name in stashed,
            )
            for name in names
        )
        return StoreUse(branches=branches, worktrees=owned)

    def release(self, url: str, *, branches: Sequence[str]) -> RepoRelease:
        """Release ``url``'s store repo for workspace, deleting the local ``branches`` named.

        The store deletes workspace's refs (``refs/remotes/origin/*``, its
        ``HEAD``, ``refs/tags/*``) and its private file; the repo itself goes
        when nobody else holds anything in it.
        """
        from untaped_git.api import Released  # noqa: PLC0415

        outcome = self._store(url).release(branches=branches)
        if isinstance(outcome, Released):
            return RepoRelease(action="released", detail=f"kept: {outcome.kept()}")
        return RepoRelease(
            action="removed",
            detail=f"{size_text(outcome.freed_bytes)} freed",
            freed_bytes=outcome.freed_bytes,
        )

    # -- removal -----------------------------------------------------------

    def _recheck(self, store: RepoStore, dest: Path) -> None:
        """Refuse (``conflict``) when work appeared in ``dest`` since the caller's status check.

        Run right before ``worktree remove``: changes, stashes on the
        checked-out branch, or unpushed commits block it.
        """
        out = self._in(dest, ["status", "--porcelain=v2", "--branch"], capture=True).text
        head, _, _, _, modified, untracked = _parse_status(out)
        status = WorktreeStatus(
            branch=head,
            upstream=None,
            ahead=0,
            behind=0,
            modified=modified,
            untracked=untracked,
            stashed=self._stashed(dest, head, common=store.path),
            unpushed=self._unpushed(dest),
        )
        blockers = archive_blockers(status)
        if blockers:
            raise GitError(
                f"{dest}: {', '.join(blockers)}; nothing removed",
                category="conflict",
                hint="the repo changed since the check; run status and archive again",
            )

    def _remove_worktree(self, store: RepoStore, dest: Path, *, force: bool) -> None:
        """``git worktree remove``; forced, a worktree git refuses is deleted outright.

        ``--force`` twice also removes a locked worktree or one with
        submodules. Git still refuses a worktree the store repo does not know
        (it was recreated) or cannot read; forced, that directory is deleted
        directly.
        """
        flags = ["--force", "--force"] if force else []
        try:
            store.run(["worktree", "remove", *flags, str(dest.absolute())])
        except GitError:
            if not force:
                raise
            _delete_tree(dest)

    def _owner(self, worktree: Path) -> str | None:
        """The plugin owning ``worktree`` (its ``untaped.owner``), ``None`` for a hand-made one."""
        args = ["config", "--worktree", "--get", "untaped.owner"]
        result = self._in(worktree, args, capture=True, check=False)
        if result.returncode != 0:
            return None
        return result.text.strip() or None

    def _stash_branches(self, store: RepoStore) -> set[str]:
        """Branches stash entries were made on (``WIP on <b>:``, ``On <b>:``)."""
        exists = store.run(["for-each-ref", "--format=%(refname)", "refs/stash"], capture=True)
        if not exists.text.strip():
            return set()
        args = ["reflog", "show", "--format=%gs", "refs/stash"]
        subjects = store.run(args, capture=True, check=False).text.splitlines()
        branches = set()
        for subject in subjects:
            for prefix in ("WIP on ", "On "):
                if subject.startswith(prefix) and ":" in subject:
                    branches.add(subject.removeprefix(prefix).partition(":")[0])
        return branches

    def _unpushed_on(self, store: RepoStore, branch: str) -> int:
        """Commits on ``refs/heads/<branch>`` that no ``refs/remotes/origin/*`` has."""
        tip = f"refs/heads/{branch}"
        args = ["rev-list", "--count", tip, "--not", "--glob=refs/remotes/origin/*"]
        return int(store.run(args, capture=True).text.strip() or 0)

    # -- checkout steps ----------------------------------------------------

    def _default_branch(self, store: RepoStore, url: str) -> str:
        """The default branch: the store's ``origin/HEAD``, else asked, else ``main``."""
        from untaped_git.api import default_branch  # noqa: PLC0415

        args = ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"]
        head = store.run(args, capture=True, check=False).text.strip().removeprefix("origin/")
        if head:
            return head
        try:
            return default_branch(url) or "main"
        except UntapedError:
            return "main"  # the fetch just worked: a missing base names the problem better

    def _worktree_branches(self, store: RepoStore) -> set[str] | None:
        """Branches checked out in registered worktrees; ``None`` when there are none."""
        out = store.run(["worktree", "list", "--porcelain"], capture=True).text
        lines = out.splitlines()
        if sum(1 for line in lines if line.startswith("worktree ")) <= 1:
            return None
        return {line.removeprefix("branch ") for line in lines if line.startswith("branch ")}

    def _refs(self, store: RepoStore, branch: str | None, base: str) -> dict[str, str]:
        """Object ids of whichever of the branch (local and origin) and base refs exist."""
        names = [f"refs/remotes/origin/{base}"]
        if branch is not None:
            names += [f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"]
        args = ["for-each-ref", "--format=%(refname) %(objectname)", *names]
        out = store.run(args, capture=True).text
        pairs = (line.partition(" ") for line in out.splitlines())
        return {ref: oid for ref, _, oid in pairs if ref in names}

    def _add_writable(
        self, store: RepoStore, target: Path, branch: str, base: str, refs: dict[str, str]
    ) -> Checkout:
        local = refs.get(f"refs/heads/{branch}")
        remote = refs.get(f"refs/remotes/origin/{branch}")
        tracking = f"origin/{branch}"
        if local and f"refs/heads/{branch}" in (self._worktree_branches(store) or set()):
            raise GitError(
                f"branch '{branch}' is already checked out in another worktree",
                category="conflict",
                hint=_BRANCH_IN_USE_HINT,
            )
        if local and remote:
            detail = self._reconcile(store, branch, same=local == remote)
            store.worktree_add(target, f"refs/heads/{branch}")
            return Checkout(action="checked_out", base=base, detail=detail)
        if remote:
            store.worktree_add(target, f"refs/remotes/{tracking}", branch=branch)
            return Checkout(action="checked_out", base=base, detail=f"tracking {tracking}")
        if local:
            store.worktree_add(target, f"refs/heads/{branch}")
            return Checkout(action="checked_out", base=base, detail="resumed local branch")
        store.worktree_add(target, f"refs/remotes/origin/{base}", branch=branch)
        return Checkout(action="created", base=base, detail=f"from origin/{base}")

    def _reconcile(self, store: RepoStore, branch: str, *, same: bool) -> str:
        """Fast-forward a local branch behind its remote; describe how the two relate."""
        tracking = f"origin/{branch}"
        if same:
            return f"tracking {tracking}"
        if self._is_ancestor(store, branch, tracking):
            store.run(["branch", "-f", branch, tracking])
            return f"tracking {tracking}"
        if self._is_ancestor(store, tracking, branch):
            return f"resumed; ahead of {tracking}"
        return f"resumed; diverged from {tracking}"

    def _set_upstream(self, store: RepoStore, branch: str) -> None:
        """``origin/<branch>`` as the upstream, so the first ``git push`` creates it."""
        store.run(["config", f"branch.{branch}.remote", "origin"])
        store.run(["config", f"branch.{branch}.merge", f"refs/heads/{branch}"])

    # -- history backfill --------------------------------------------------

    def _history(self, store: RepoStore) -> History:
        """The backfill state in ``untaped-workspace.json``; writes ``partial`` when it is new.

        The file is also how the store's report knows workspace uses the repo.
        """
        try:
            recorded = json.loads(store.private_file.read_text(encoding="utf-8")).get("history")
        except OSError, ValueError, AttributeError:
            recorded = None
        if recorded == "complete":
            return "complete"
        if recorded != "partial":
            self._record_history(store, "partial")
        return "partial"

    def _record_history(self, store: RepoStore, history: History) -> None:
        try:
            atomic_write(store.private_file, json.dumps({"history": history}) + "\n")
        except OSError as exc:
            raise WorkspaceError(
                f"could not write {store.private_file}: {exc.strerror or exc}",
                category="failed",
                system="local",
            ) from exc

    def _backfill(self, store: RepoStore, delta: RefDelta, history: History) -> str | None:
        """Bring the blobs of the history the refs reach; why it stopped, else ``None``.

        A ``partial`` history walks every ref whole (an interrupted backfill
        resumes where its packs left off); a ``complete`` one walks only what
        this fetch moved (``<old>..<new>``) or added, so a fetch that brought
        nothing walks nothing.
        """
        if history == "complete":
            revisions = [f"{move.old}..{move.new}" for move in delta.moved.values()]
            revisions += delta.added.values()
            if not revisions:
                return None
        else:
            revisions = list(HISTORY)
        try:
            store.prefetch(history=revisions)
        except UntapedError as exc:
            if history == "complete":
                self._record_history(store, "partial")
            return str(exc)
        if history == "partial":
            self._record_history(store, "complete")
        return None

    # -- status helpers ----------------------------------------------------

    def _stashed(self, wt: Path, branch: str | None, *, common: Path) -> int:
        """Stash entries made on ``branch``; no git run when ``common`` has no stash."""
        if not any((common / marker).exists() for marker in _STASH_MARKERS):
            return 0
        label = branch if branch is not None else "(no branch)"
        out = self._in(wt, ["stash", "list", "--format=%gs"], capture=True).text
        prefixes = (f"WIP on {label}:", f"On {label}:")
        return sum(1 for line in out.splitlines() if line.startswith(prefixes))

    def _unpushed(self, wt: Path) -> int:
        """Commits on ``HEAD`` that no remote-tracking branch has (detached or not)."""
        args = ["rev-list", "--count", "HEAD", "--not", "--remotes"]
        out = self._in(wt, args, capture=True).text
        return int(out.strip() or 0)

    def _has_submodules(self, wt: Path) -> bool:
        """Whether any submodule is initialised (``-`` marks an uninitialised one)."""
        out = self._in(wt, ["submodule", "status", "--recursive"], capture=True).text
        return any(line and not line.startswith("-") for line in out.splitlines())

    def _check_registered(self, dest: Path) -> Path:
        """Raise unless ``dest`` is the worktree its git admin directory points back to.

        Returns the worktree's common git directory (its store repo).

        A recreated store repo can leave an old worktree pointing at an admin
        directory that now belongs to another worktree with the same name;
        reading it would report that other worktree's state.
        """
        out = self._in(dest, ["rev-parse", "--absolute-git-dir"], capture=True).text
        admin = Path(out.strip())
        try:
            back = (admin / (admin / "gitdir").read_text().strip()).resolve()
        except OSError:
            back = None
        if back != (dest / ".git").resolve():
            raise GitError(
                f"{dest} is not registered as a worktree of its repo",
                category="failed",
                hint=(
                    "if the workspace directory moved, run git worktree repair in the repo; "
                    "otherwise archive with --force after checking it"
                ),
            )
        try:
            return (admin / (admin / "commondir").read_text().strip()).resolve()
        except OSError:
            return admin

    # -- plumbing ----------------------------------------------------------

    def _store(self, url: str) -> RepoStore:
        from untaped_git.api import RepoStore  # noqa: PLC0415  # keeps CLI startup free of git
        from untaped_workspace import SPEC  # noqa: PLC0415

        return RepoStore.for_url(url, plugin=SPEC, error=GitError, map_error=_git_error)

    def _in(
        self, worktree: Path, args: Sequence[str], *, capture: bool = False, check: bool = True
    ) -> GitResult:
        """Run ``git <args>`` in ``worktree`` (no auth: these commands never fetch)."""
        try:
            return run_git(
                args,
                cwd=worktree,
                git=self._git,
                timeout=self._timeout,
                capture=capture,
                check=check,
                ceiling=True,
            )
        except GitCommandError as exc:
            raise _git_error(exc) from exc

    def _has_ref(self, wt: Path, ref: str) -> bool:
        args = ["show-ref", "--verify", "--quiet", ref]
        return self._in(wt, args, check=False).returncode == 0

    def _is_ancestor(self, store: RepoStore, ancestor: str, descendant: str) -> bool:
        args = ["merge-base", "--is-ancestor", ancestor, descendant]
        return store.run(args, check=False).returncode == 0


def _delete_tree(path: Path) -> None:
    """Delete ``path`` (gone already is fine); failures are attributed locally."""
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise WorkspaceError(
            f"could not delete {path}: {exc.strerror or exc}",
            category="failed",
            system="local",
            hint="check its permissions and that no process holds it, then retry",
        ) from exc


def _git_error(exc: GitCommandError) -> GitError:
    """Map a failed git run; a branch or directory already in use is a conflict."""
    stderr = exc.stderr.lower()
    if any(marker in stderr for marker in _BRANCH_IN_USE):
        return GitError(
            str(exc), returncode=exc.returncode, category="conflict", hint=_BRANCH_IN_USE_HINT
        )
    if "already exists" in stderr:
        return GitError(str(exc), returncode=exc.returncode, category="conflict", hint=_EXISTS_HINT)
    return GitError(str(exc), returncode=exc.returncode, **attribution(exc))


def _parse_status(out: str) -> tuple[str | None, str | None, int, int, int, int]:
    """Parse ``git status --porcelain=v2 --branch`` output.

    Returns ``(branch, upstream, ahead, behind, modified, untracked)``.
    """
    branch: str | None = None
    upstream: str | None = None
    ahead = 0
    behind = 0
    modified = 0
    untracked = 0
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head ") :].strip()
            branch = None if head == "(detached)" else head
        elif line.startswith("# branch.upstream "):
            upstream = line[len("# branch.upstream ") :].strip() or None
        elif line.startswith("# branch.ab "):
            parts = line[len("# branch.ab ") :].split()
            for p in parts:
                if p.startswith("+"):
                    ahead = int(p[1:])
                elif p.startswith("-"):
                    behind = int(p[1:])
        elif line.startswith("?"):
            untracked += 1
        elif line.startswith(("1 ", "2 ", "u ")):
            modified += 1
    return branch, upstream, ahead, behind, modified, untracked
