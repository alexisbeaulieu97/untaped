"""Local bare Git corpus adapter for sweep and cache commands."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

from untaped.capabilities.github.domain import (
    CorpusFreshness,
    CorpusRepoResult,
    CorpusRepoTarget,
    GrepHit,
    GrepSpec,
    LocalRef,
    RefProfile,
    RefSelector,
    WorktreeResult,
    profile_join,
)
from untaped.capabilities.github.errors import GitCorpusError
from untaped.sdk import (
    GitCommandError,
    GitResult,
    RepoCache,
    atomic_write,
    attribution,
    cache_path,
    list_caches,
    run_git,
    safe_path_segment,
    ui_context,
)

DEFAULT_TIMEOUT = 60.0
DEFAULT_SLOW_TIMEOUT = 600.0
DEFAULT_FETCH_ATTEMPTS = 3
DEFAULT_FETCH_BATCH_SIZE = 50
DEFAULT_LOCK_TIMEOUT = 600.0
METADATA_FILE = "untaped-corpus.json"


class GitCorpusCache:
    """Maintain a managed bare Git corpus and search it with ``git grep``.

    ``auth_host`` is the only host the auth header is sent to (the Git host
    of ``github.base_url``); a remote on any other host, or any remote when
    it is None, is fetched without credentials. It is required so a fetching
    caller cannot silently drop auth; callers that never fetch pass None.
    """

    def __init__(
        self,
        *,
        auth_host: str | None,
        git: str = "git",
        timeout: float = DEFAULT_TIMEOUT,
        slow_timeout: float = DEFAULT_SLOW_TIMEOUT,
        fetch_attempts: int = DEFAULT_FETCH_ATTEMPTS,
        fetch_batch_size: int = DEFAULT_FETCH_BATCH_SIZE,
        lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
        sleep: Callable[[float], None] = time.sleep,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._auth_host = auth_host
        self._git = git
        self._timeout = timeout
        self._slow_timeout = slow_timeout
        self._fetch_attempts = fetch_attempts
        self._fetch_batch_size = fetch_batch_size
        self._lock_timeout = lock_timeout
        self._sleep = sleep
        self._warn = warn or _ui_warning

    def sync_repo(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        selector: RefSelector,
        depth: int,
        auth_header: str | None,
    ) -> CorpusRepoResult:
        """Fetch the requested ref profile into the managed bare corpus."""
        url = _remote_url(repo)
        cache = self._cache(repo, root=root, auth_header=auth_header)
        with cache.locked():
            branch = _default_branch(repo)
            cache.ensure(url)

            stored = self.repo_freshness(repo, root=root)
            profile = profile_join(stored.profile, selector.profile) if stored else selector.profile
            ref_globs = tuple(
                dict.fromkeys((*(stored.ref_globs if stored else ()), *selector.globs))
            )
            effective = RefSelector(profile=profile, globs=ref_globs)

            if effective.beyond_default():
                self._sync_selected_refs(
                    cache, depth=depth, selector=effective, default_branch=branch
                )
            else:
                cache.fetch(
                    (f"+refs/heads/{branch}:refs/heads/{branch}",),
                    prune=True,
                    tags=False,
                    depth=depth,
                )
                self._prune_uncovered_refs(cache, selector=effective, default_branch=branch)

            fetched_at = datetime.now(UTC).isoformat()
            _write_metadata(
                cache.path,
                {
                    "repo": repo.full_name,
                    "ref": branch,
                    "clone_url": url,
                    "fetched_at": fetched_at,
                    "profile": effective.profile,
                    "ref_globs": list(effective.globs),
                    "archived": repo.archived,
                    # A source without pushed_at must not erase the stored one.
                    "pushed_at": repo.pushed_at or (stored.pushed_at if stored else None),
                },
            )
            return CorpusRepoResult(
                repo=repo.full_name,
                ref=branch,
                path=str(cache.path),
                clone_url=url,
                status="synced",
                fetched_at=fetched_at,
                profile=effective.profile,
                ref_globs=effective.globs,
                archived=repo.archived,
            )

    def repo_freshness(self, repo: CorpusRepoTarget, *, root: Path) -> CorpusFreshness | None:
        """Return cached fetch metadata for ``repo`` if present."""
        metadata_path = _bare_path(repo, root=root) / METADATA_FILE
        if not metadata_path.is_file():
            return None
        data = _read_metadata(metadata_path)
        fetched_at = _optional_str(data.get("fetched_at"))
        if fetched_at is None:
            return None
        try:
            fetched = datetime.fromisoformat(fetched_at)
        except ValueError as exc:
            raise GitCorpusError(
                f"could not read corpus metadata {metadata_path}: invalid fetched_at"
            ) from exc
        return CorpusFreshness(
            fetched_at=fetched,
            profile=_metadata_profile(data),
            ref_globs=_metadata_ref_globs(data),
            archived=_metadata_archived(data),
            pushed_at=_optional_str(data.get("pushed_at")),
            default_branch=_optional_str(data.get("ref")),
        )

    def touch_repo(self, repo: CorpusRepoTarget, *, root: Path) -> datetime:
        """Mark a cached copy current without fetching (GitHub reports no new push)."""
        cache = self._cached(repo, root=root)
        with cache.locked():
            data = _read_metadata(cache.path / METADATA_FILE)
            fetched = datetime.now(UTC)
            data.update(
                fetched_at=fetched.isoformat(), archived=repo.archived, pushed_at=repo.pushed_at
            )
            _write_metadata(cache.path, data)
        return fetched

    def local_refs(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        selector: RefSelector,
    ) -> tuple[LocalRef, ...]:
        """Return selected refs with their tree OIDs, default branch first.

        Full names keep a branch and a tag that share a short name distinct.
        Annotated tags are peeled to their commit's tree; a ref whose tree
        cannot be read that way keeps its own name as the tree-ish.
        """
        branch = _default_branch(repo)
        cache = self._cache(repo, root=root)
        if not cache.exists():
            return ()
        result = cache.run(
            ["for-each-ref", "--format=%(refname) %(tree) %(*tree)", "refs/heads", "refs/tags"],
            capture=True,
        )
        trees: dict[str, str] = {}
        for line in result.text.splitlines():
            ref, _, rest = line.partition(" ")
            if _selector_covers_ref(selector, ref, default_branch=branch):
                trees.setdefault(ref, next(iter(rest.split()), ref))
        ordered = _order_refs(tuple(trees), default_branch=f"refs/heads/{branch}")
        return tuple(LocalRef(name=ref, tree=trees[ref]) for ref in ordered)

    def grep_trees(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        trees: tuple[str, ...],
        spec: GrepSpec,
    ) -> dict[str, tuple[GrepHit, ...]]:
        """Run one ``git grep`` over several cached trees; trees without hits are absent."""
        if not trees:
            return {}
        cache = self._cached(repo, root=root)
        result = self._grep(cache, ["-n", "--column", "-z"], spec, trees)
        if result.returncode == 1:
            return {}
        hits: dict[str, list[GrepHit]] = {}
        for tree, path, line, text in _parse_grep_output(result.stdout, trees=trees):
            hits.setdefault(tree, []).append(GrepHit(path=path, line=line, text=text))
        return {tree: tuple(rows) for tree, rows in hits.items()}

    def tree_has_match(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        tree: str,
        spec: GrepSpec,
    ) -> bool:
        """Return whether ``spec`` matches in ``tree``; ``git grep -q`` stops at the first hit."""
        cache = self._cached(repo, root=root)
        return self._grep(cache, ["-q"], spec, (tree,)).returncode == 0

    def _grep(
        self, cache: RepoCache, mode: list[str], spec: GrepSpec, trees: tuple[str, ...]
    ) -> GitResult:
        """Run ``git grep`` with the sweep's pinned flags; exit 1 (no match) is not an error."""
        args = ["grep", *mode, "-I"]
        if spec.ignore_case:
            args.append("--ignore-case")
        # Pin the pattern syntax so a user's grep.patternType cannot change results.
        args.append("--fixed-strings" if spec.fixed_strings else "--extended-regexp")
        if spec.word_regexp:
            args.append("--word-regexp")
        args.extend(["-e", spec.pattern, *trees, "--", *spec.paths])
        # Keep the user's locale: it decides how the regex treats non-ASCII text.
        result = cache.run(args, capture=True, check=False, locale_c=False)
        if result.returncode not in {0, 1}:
            stderr = result.stderr.strip()
            raise GitCorpusError(stderr or f"git grep failed with status {result.returncode}")
        return result

    def tree_paths(self, repo: CorpusRepoTarget, *, root: Path, ref: str) -> tuple[str, ...]:
        """List paths in one cached ref tree."""
        cache = self._cached(repo, root=root)
        result = cache.run(["ls-tree", "-r", "--name-only", "-z", ref], capture=True)
        return tuple(part.decode(errors="replace") for part in result.stdout.split(b"\0") if part)

    def read_first_blob(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        ref: str,
        paths: tuple[str, ...],
    ) -> str | None:
        """Read the first of ``paths`` that is a blob in one cached ref, in one git call."""
        cache = self._cached(repo, root=root)
        result = cache.run(
            ["cat-file", "--batch"],
            capture=True,
            stdin="".join(f"{ref}:{path}\n" for path in paths),
        )
        return _first_blob(result.stdout)

    def validate_pattern(
        self,
        *,
        root: Path,
        pattern: str,
        paths: tuple[str, ...],
        fixed_strings: bool,
    ) -> str | None:
        """Validate one grep pattern and its pathspecs against an empty scratch directory."""
        managed_root = root.expanduser()
        managed_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".validate-", dir=managed_root) as scratch:
            scratch_path = Path(scratch)
            # --no-index needs no repository, so validation costs one git call.
            args = ["grep", "--no-index", "-n"]
            args.append("--fixed-strings" if fixed_strings else "--extended-regexp")
            args.extend(["-e", pattern, "--"])
            args.extend(paths)
            # No cache here, so no auth either: the one direct ``run_git`` call.
            try:
                result = run_git(
                    args,
                    cwd=scratch_path,
                    git=self._git,
                    timeout=self._timeout,
                    check=False,
                    locale_c=False,
                )
            except GitCommandError as exc:
                raise GitCorpusError(str(exc), **attribution(exc)) from exc
        if result.returncode in {0, 1}:
            return None
        return result.stderr.strip()

    def list_repos(self, *, root: Path) -> tuple[CorpusRepoResult, ...]:
        """List repositories with corpus metadata under ``root``, sorted by name."""
        managed_root = root.expanduser()
        if not managed_root.exists():
            return ()
        rows: list[CorpusRepoResult] = []
        for metadata_path, data in self._metadata_entries(managed_root):
            bare = metadata_path.parent
            rows.append(
                CorpusRepoResult(
                    repo=str(data.get("repo") or ""),
                    ref=str(data.get("ref") or ""),
                    path=str(bare),
                    clone_url=_optional_str(data.get("clone_url")),
                    status="cached",
                    fetched_at=_optional_str(data.get("fetched_at")),
                    profile=_metadata_profile(data),
                    ref_globs=_metadata_ref_globs(data),
                    archived=_metadata_archived(data),
                )
            )
        # By name: host-less caches (`file://`, local paths) are keyed by a hash.
        return tuple(sorted((row for row in rows if row.repo and row.ref), key=_by_name))

    def get_repo(self, *, root: Path, repo: str) -> CorpusRepoTarget | None:
        """Return cached repository metadata for ``repo`` if present."""
        managed_root = root.expanduser()
        if not managed_root.exists():
            return None
        for _metadata_path, data in self._metadata_entries(managed_root):
            if data.get("repo") != repo:
                continue
            return CorpusRepoTarget(
                full_name=repo,
                default_branch=_optional_str(data.get("ref")),
                clone_url=_optional_str(data.get("clone_url")),
                archived=_metadata_archived(data),
            )
        return None

    def _metadata_entries(self, managed_root: Path) -> list[tuple[Path, dict[str, object]]]:
        """Read every managed bare repo's metadata, warning on and skipping corrupt files."""
        entries: list[tuple[Path, dict[str, object]]] = []
        for bare in list_caches(managed_root, skip=("worktrees",)):
            metadata_path = bare / METADATA_FILE
            if not metadata_path.is_file():
                continue
            try:
                entries.append((metadata_path, _read_metadata(metadata_path)))
            except GitCorpusError as exc:
                self._warn(str(exc))
        return entries

    def clean_repo(self, *, root: Path, repo: CorpusRepoResult) -> CorpusRepoResult:
        """Remove one cached repository from the managed corpus root."""
        managed_root = root.expanduser().resolve()
        bare = Path(repo.path).expanduser().resolve()
        if not bare.is_relative_to(managed_root):
            raise GitCorpusError(f"refusing to remove path outside managed root: {bare}")
        cache = self._open(bare)
        with cache.locked():
            self._remove_managed_worktrees(cache, managed_root=managed_root)
            shutil.rmtree(bare)
        Path(f"{bare}.lock").unlink(missing_ok=True)
        return repo.model_copy(update={"status": "removed"})

    def materialize_worktree(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        ref: str | None,
    ) -> WorktreeResult:
        """Materialize one cached repository ref into a managed worktree."""
        branch = _default_branch(repo)
        selected_ref = ref or branch
        cache = self._cached(repo, root=root)
        if not self._ref_exists(cache, selected_ref):
            raise GitCorpusError(
                f"ref is not cached: {selected_ref}; run a sweep that fetches it",
                category="not_found",
            )
        worktree = _worktree_path(repo.full_name, selected_ref, root=root)
        if worktree.exists() and not (worktree / ".git").exists():
            raise GitCorpusError(f"worktree path exists and is not a git worktree: {worktree}")
        if worktree.exists():
            cache.run(
                ["checkout", "--detach", selected_ref], cwd=worktree, timeout=self._slow_timeout
            )
        else:
            worktree.parent.mkdir(parents=True, exist_ok=True)
            cache.run(
                ["worktree", "add", "--detach", str(worktree), selected_ref],
                timeout=self._slow_timeout,
            )
        return WorktreeResult(repo=repo.full_name, ref=selected_ref, path=str(worktree))

    def _cache(
        self, repo: CorpusRepoTarget, *, root: Path, auth_header: str | None = None
    ) -> RepoCache:
        """The cache of ``repo``'s remote; ``RepoCache`` scopes the token to ``auth_host``."""
        url = _remote_url(repo)
        return self._open(cache_path(url, root=root), _check_auth_url(url, auth_header))

    def _cached(self, repo: CorpusRepoTarget, *, root: Path) -> RepoCache:
        """The cache of ``repo``; ``not_found`` when it has never been synced."""
        cache = self._cache(repo, root=root)
        if not cache.exists():
            raise GitCorpusError("repository is not in the local corpus", category="not_found")
        return cache

    def _open(self, path: Path, auth_header: str | None = None) -> RepoCache:
        # A sweep runs unattended across many repos: git never prompts, and
        # uncaptured stdout is discarded so stray git chatter cannot corrupt
        # piped output.
        return RepoCache(
            path,
            error=GitCorpusError,
            auth_header=auth_header,
            auth_host=self._auth_host,
            git=self._git,
            timeout=self._timeout,
            slow_timeout=self._slow_timeout,
            lock_timeout=self._lock_timeout,
            attempts=self._fetch_attempts,
            sleep=self._sleep,
        )

    def _sync_selected_refs(
        self,
        cache: RepoCache,
        *,
        depth: int,
        selector: RefSelector,
        default_branch: str,
    ) -> None:
        """Mirror the selected remote refs in bounded, individually retried batches.

        Wide profiles can select hundreds of refs; one fetch for all of them
        streams a single huge pack that a dropped connection discards whole.
        Listing the remote first lets unchanged refs skip the network, lets
        each batch land independently (a failed run resumes where it
        stopped), and replaces ``--prune`` for refs deleted upstream.
        """
        remote = {
            ref: oid
            for ref, oid in self._remote_refs(cache).items()
            if _selector_covers_ref(selector, ref, default_branch=default_branch)
        }
        local = self._local_ref_oids(cache)
        cache.delete_refs(ref for ref in local if ref not in remote)
        wanted = sorted(ref for ref, oid in remote.items() if local.get(ref) != oid)
        for start in range(0, len(wanted), self._fetch_batch_size):
            batch = wanted[start : start + self._fetch_batch_size]
            cache.fetch(
                tuple(f"+{ref}:{ref}" for ref in batch), prune=False, tags=False, depth=depth
            )

    def _remote_refs(self, cache: RepoCache) -> dict[str, str]:
        result = cache.run(
            ["ls-remote", "--heads", "--tags", "--refs", "origin"],
            capture=True,
            timeout=self._slow_timeout,
            retry=True,
        )
        refs: dict[str, str] = {}
        for line in result.text.splitlines():
            oid, _, ref = line.partition("\t")
            if ref:
                refs[ref] = oid
        return refs

    def _local_ref_oids(self, cache: RepoCache) -> dict[str, str]:
        result = cache.run(
            ["for-each-ref", "--format=%(objectname) %(refname)", "refs/heads", "refs/tags"],
            capture=True,
        )
        refs: dict[str, str] = {}
        for line in result.text.splitlines():
            oid, _, ref = line.partition(" ")
            if ref:
                refs[ref] = oid
        return refs

    def _prune_uncovered_refs(
        self,
        cache: RepoCache,
        *,
        selector: RefSelector,
        default_branch: str,
    ) -> None:
        result = cache.run(
            ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags"],
            capture=True,
        )
        cache.delete_refs(
            ref
            for ref in result.text.splitlines()
            if not _selector_covers_ref(selector, ref, default_branch=default_branch)
        )

    def _ref_exists(self, cache: RepoCache, ref: str) -> bool:
        result = cache.run(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], check=False)
        return result.returncode == 0

    def _remove_managed_worktrees(self, cache: RepoCache, *, managed_root: Path) -> None:
        bare = cache.path
        result = cache.run(["worktree", "list", "--porcelain"], capture=True, check=False)
        if result.returncode != 0:
            return
        for line in result.text.splitlines():
            if not line.startswith("worktree "):
                continue
            worktree = Path(line.removeprefix("worktree ")).expanduser().resolve()
            if worktree == bare or not worktree.is_relative_to(managed_root / "worktrees"):
                continue
            removed = cache.run(
                ["worktree", "remove", "--force", str(worktree)],
                check=False,
                timeout=self._slow_timeout,
            )
            if removed.returncode != 0 and worktree.exists():
                shutil.rmtree(worktree)
        cache.run(["worktree", "prune"], check=False)


def _ui_warning(message: str) -> None:
    ui_context(strict=False).message("warning", message)


def _bare_path(repo: CorpusRepoTarget, *, root: Path) -> Path:
    """Return the bare-cache path for ``repo``'s remote URL (see ``cache_path``)."""
    return cache_path(_remote_url(repo), root=root)


def _default_branch(repo: CorpusRepoTarget) -> str:
    if not repo.default_branch:
        raise GitCorpusError(f"repository metadata missing default_branch: {repo.full_name}")
    return repo.default_branch


def _remote_url(repo: CorpusRepoTarget) -> str:
    if repo.clone_url:
        return repo.clone_url
    if repo.html_url:
        return f"{repo.html_url.removesuffix('/')}.git"
    return f"https://github.com/{repo.full_name}.git"


def _selector_covers_ref(selector: RefSelector, ref: str, *, default_branch: str) -> bool:
    if ref.startswith("refs/heads/"):
        name = ref.removeprefix("refs/heads/")
        if selector.profile in {"branches", "all"} or name == default_branch:
            return True
    elif ref.startswith("refs/tags/"):
        name = ref.removeprefix("refs/tags/")
        if selector.profile in {"tags", "all"}:
            return True
    else:
        return False
    return any(fnmatch.fnmatchcase(name, glob) for glob in selector.globs)


def _order_refs(refs: tuple[str, ...], *, default_branch: str) -> tuple[str, ...]:
    ordered = sorted(ref for ref in refs if ref != default_branch)
    if default_branch in refs:
        return (default_branch, *ordered)
    return tuple(ordered)


def _first_blob(payload: bytes) -> str | None:
    """Return the first blob in ``git cat-file --batch`` output; missing objects are skipped."""
    cursor = 0
    while cursor < len(payload):
        header, cursor = _read_until(payload, cursor, b"\n")
        parts = header.split()
        if len(parts) != 3 or not parts[2].isdigit():
            continue  # "<name> missing" (or ambiguous) carries no content
        size = int(parts[2])
        if parts[1] == b"blob":
            return payload[cursor : cursor + size].decode(errors="replace")
        cursor += size + 1
    return None


def _check_auth_url(url: str, auth_header: str | None) -> str | None:
    """Refuse a token with an ssh/scp ``url``; ``RepoCache`` scopes it to the host otherwise."""
    if auth_header is None:
        return None
    parsed = urlparse(url)
    if (parsed.scheme == "https" and parsed.netloc) or parsed.scheme == "file":
        return auth_header
    if parsed.scheme or url.startswith("git@"):
        raise GitCorpusError("authenticated Git corpus sync requires an HTTPS clone_url")
    return auth_header


def _parse_grep_output(
    payload: bytes, *, trees: tuple[str, ...]
) -> tuple[tuple[str, str, int, str], ...]:
    """Parse ``git grep -n --column -z`` output over tree-ishes into (tree, path, line, text)."""
    rows: list[tuple[str, str, int, str]] = []
    cursor = 0
    while cursor < len(payload):
        raw_ref_path, cursor = _read_until(payload, cursor, b"\0")
        raw_line, cursor = _read_until(payload, cursor, b"\0")
        _raw_column, cursor = _read_until(payload, cursor, b"\0")
        raw_text, cursor = _read_until(payload, cursor, b"\n")
        # Neither a tree OID nor a refname contains ":", so the first one splits.
        tree, sep, path = raw_ref_path.decode(errors="replace").partition(":")
        if not sep or tree not in trees:
            raise GitCorpusError("could not parse git grep output: malformed ref/path")
        try:
            line = int(raw_line.decode())
        except ValueError as exc:
            raise GitCorpusError("could not parse git grep output: invalid line") from exc
        rows.append((tree, path, line, raw_text.decode(errors="replace").rstrip("\n")))
    return tuple(rows)


def _read_until(payload: bytes, cursor: int, delimiter: bytes) -> tuple[bytes, int]:
    end = payload.find(delimiter, cursor)
    if end == -1:
        raise GitCorpusError("could not parse git grep output")
    return payload[cursor:end], end + len(delimiter)


def _worktree_path(repo: str, ref: str, *, root: Path) -> Path:
    digest = hashlib.sha256(f"{repo}@{ref}".encode()).hexdigest()[:12]
    name = f"{safe_path_segment(repo)}-{safe_path_segment(ref)}-{digest}"
    return root.expanduser() / "worktrees" / name


def _write_metadata(path: Path, data: dict[str, object]) -> None:
    atomic_write(path / METADATA_FILE, json.dumps(data, sort_keys=True) + "\n")


def _read_metadata(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text())
    except OSError as exc:
        raise GitCorpusError(f"could not read corpus metadata {path}: {exc}") from exc
    except ValueError as exc:
        raise GitCorpusError(f"could not read corpus metadata {path}: invalid JSON") from exc
    if not isinstance(data, dict):
        raise GitCorpusError(f"could not read corpus metadata {path}: expected object")
    return data


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _metadata_profile(data: dict[str, object]) -> RefProfile:
    value = data.get("profile")
    if value in {"default", "branches", "tags", "all"}:
        return cast(RefProfile, value)
    return "default"


def _metadata_ref_globs(data: dict[str, object]) -> tuple[str, ...]:
    value = data.get("ref_globs")
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _metadata_archived(data: dict[str, object]) -> bool:
    return bool(data.get("archived", False))


def _by_name(row: CorpusRepoResult) -> tuple[str, str]:
    return (row.repo, row.path)
