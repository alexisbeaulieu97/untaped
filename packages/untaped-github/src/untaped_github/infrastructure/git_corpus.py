"""The sweep's local Git corpus, kept in the git plugin's repo store.

Each repo the sweep and the ``cache`` commands use is a store repo
(``RepoStore.for_url(url, plugin=SPEC)``), which other plugins may share:
github's refs live in its own namespace, ``refs/untaped/github/heads/*`` and
``refs/untaped/github/tags/*``, its fetch metadata in the repo's
``untaped-github.json``, and its materialised worktrees under
``~/.untaped/plugins/github/worktrees/``. Rows and outputs keep plain ref names
(``refs/heads/<branch>``, ``refs/tags/<tag>``). The store fetches full history
blobless and holds the credentials (github's own ``GithubHost`` answers for
the GitHub host), so nothing here sees a token or a depth.

Blobs are read only through a ``prefetched()`` handle. Its prefetch is
limited to the grep's pathspecs, with one widening: ``rev-list --objects``,
which lists what the prefetch fetches, does not match a wildcard across
directories as ``git grep`` does (``*.py`` lists top-level files only), so a
wildcard pathspec prefetches its literal leading directory, or the whole
tree when it has none; magic other than ``exclude`` prefetches the whole tree.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import tempfile
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse

from untaped.sdk import (
    GitCommandError,
    GitResult,
    UntapedError,
    atomic_write,
    attribution,
    plugin_dir,
    run_git,
    safe_path_segment,
    ui_context,
)
from untaped_git.api import Removed, RepoStore, ls_remote, store_key
from untaped_github import SPEC
from untaped_github.domain import (
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
from untaped_github.errors import GitCorpusError

DEFAULT_TIMEOUT = 60.0
_PLAIN_ROOTS = ("refs/heads/", "refs/tags/")
#: A ``--ref`` glob the store takes as it is: one ``*`` at most, plain ref characters.
_STORE_GLOB = re.compile(r"[\w.\-/]*\*?[\w.\-/]*")
_WILDCARD = re.compile(r"[*?\[\\]")
_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

type GitProtocol = Literal["https", "ssh"]


class GitCorpusCache:
    """Sync, list and search github's repos in the repo store.

    ``web_host`` is the Git host of ``github.base_url``; with ``protocol``
    ``ssh`` a repo on it is fetched over ``git@<web_host>:<owner>/<name>.git``
    (the store keeps one repo for both spellings). ``git`` and ``timeout``
    apply to pattern validation, the one git run outside the store.
    """

    def __init__(
        self,
        *,
        web_host: str | None = None,
        protocol: GitProtocol = "https",
        git: str = "git",
        timeout: float = DEFAULT_TIMEOUT,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self._web_host = web_host
        self._protocol = protocol
        self._git = git
        self._timeout = timeout
        self._warn = warn or _ui_warning

    def sync_repo(self, repo: CorpusRepoTarget, *, selector: RefSelector) -> CorpusRepoResult:
        """Fetch the requested ref profile, widened by what was fetched before, into the store."""
        url = self._remote_url(repo)
        branch = _default_branch(repo)
        store = self._store(url)
        stored = _freshness(store)
        profile = profile_join(stored.profile, selector.profile) if stored else selector.profile
        ref_globs = tuple(dict.fromkeys((*(stored.ref_globs if stored else ()), *selector.globs)))
        effective = RefSelector(profile=profile, globs=ref_globs)
        branches, tags = self._ref_names(url, effective, default_branch=branch)
        # prune=True: refs the selector no longer covers, or the remote no longer has, go.
        store.fetch(branches=branches, tags=tags, prune=True)

        fetched_at = datetime.now(UTC).isoformat()
        _write_metadata(
            store,
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
            full_name=repo.full_name,
            ref=branch,
            path=str(store.path),
            clone_url=url,
            status="synced",
            fetched_at=fetched_at,
            profile=effective.profile,
            ref_globs=effective.globs,
            archived=repo.archived,
        )

    def repo_freshness(self, repo: CorpusRepoTarget) -> CorpusFreshness | None:
        """Return the stored fetch metadata of ``repo`` if present."""
        return _freshness(self._store(self._remote_url(repo)))

    def touch_repo(self, repo: CorpusRepoTarget) -> datetime:
        """Mark a stored copy current without fetching (GitHub reports no new push)."""
        store = self._existing(repo)
        data = _read_metadata(store.private_file)
        fetched = datetime.now(UTC)
        data.update(
            fetched_at=fetched.isoformat(), archived=repo.archived, pushed_at=repo.pushed_at
        )
        _write_metadata(store, data)
        return fetched

    def local_refs(self, repo: CorpusRepoTarget, *, selector: RefSelector) -> tuple[LocalRef, ...]:
        """Return selected refs with their tree OIDs, default branch first.

        Full names keep a branch and a tag that share a short name distinct.
        Annotated tags are peeled to their commit's tree; a ref whose tree
        cannot be read that way keeps its own (namespaced) name as the tree-ish.
        """
        branch = _default_branch(repo)
        store = self._store(self._remote_url(repo))
        if not store.exists():
            return ()
        result = store.run(
            [
                "for-each-ref",
                "--format=%(refname) %(tree) %(*tree)",
                *store.roots,
            ],
            capture=True,
        )
        trees: dict[str, str] = {}
        for line in result.text.splitlines():
            stored, _, rest = line.partition(" ")
            relative = store.relative(stored)
            ref = f"refs/{relative}" if relative is not None else None
            if ref is not None and _selector_covers_ref(selector, ref, default_branch=branch):
                trees.setdefault(ref, next(iter(rest.split()), stored))
        ordered = _order_refs(tuple(trees), default_branch=f"refs/heads/{branch}")
        return tuple(LocalRef(name=ref, tree=trees[ref]) for ref in ordered)

    def grep_trees(
        self, repo: CorpusRepoTarget, *, trees: tuple[str, ...], spec: GrepSpec
    ) -> dict[str, tuple[GrepHit, ...]]:
        """Run one ``git grep`` over several stored trees; trees without hits are absent."""
        if not trees:
            return {}
        result = self._grep(self._existing(repo), ["-n", "--column", "-z"], spec, trees)
        if result.returncode == 1:
            return {}
        hits: dict[str, list[GrepHit]] = {}
        for tree, path, line, text in _parse_grep_output(result.stdout, trees=trees):
            hits.setdefault(tree, []).append(GrepHit(path=path, line=line, text=text))
        return {tree: tuple(rows) for tree, rows in hits.items()}

    def tree_has_match(self, repo: CorpusRepoTarget, *, tree: str, spec: GrepSpec) -> bool:
        """Return whether ``spec`` matches in ``tree``; ``git grep -q`` stops at the first hit."""
        return self._grep(self._existing(repo), ["-q"], spec, (tree,)).returncode == 0

    def _grep(
        self, store: RepoStore, mode: list[str], spec: GrepSpec, trees: tuple[str, ...]
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
        handle = store.prefetched(trees=trees, paths=prefetch_paths(spec.paths))
        # Keep the user's locale: it decides how the regex treats non-ASCII text.
        result = handle.run(args, capture=True, check=False, locale_c=False)
        if result.returncode not in {0, 1}:
            stderr = result.stderr.strip()
            raise GitCorpusError(stderr or f"git grep failed with status {result.returncode}")
        return result

    def tree_paths(self, repo: CorpusRepoTarget, *, ref: str) -> tuple[str, ...]:
        """List paths in one stored tree (a tree id, or a plain ``refs/heads|tags/<x>``)."""
        store = self._existing(repo)
        stored = store.ref(ref.removeprefix("refs/")) if ref.startswith(_PLAIN_ROOTS) else ref
        return tuple(entry.path for entry in store.ls_tree(stored))

    def read_first_blob(
        self, repo: CorpusRepoTarget, *, ref: str, paths: tuple[str, ...]
    ) -> str | None:
        """Read the first of ``paths`` that is a blob in ``ref`` (``refs/heads/<branch>``)."""
        store = self._existing(repo)
        relative = ref.removeprefix("refs/")
        if not ref.startswith(_PLAIN_ROOTS) or relative not in store.refs():
            return None
        stored = store.ref(relative)
        handle = store.prefetched(trees=[stored], paths=paths)
        result = handle.run(
            ["cat-file", "--batch"],
            capture=True,
            stdin="".join(f"{stored}:{path}\n" for path in paths),
        )
        return _first_blob(result.stdout)

    def validate_pattern(
        self, *, pattern: str, paths: tuple[str, ...], fixed_strings: bool
    ) -> str | None:
        """Validate one grep pattern and its pathspecs against an empty scratch directory."""
        scratch_root = plugin_dir(SPEC)
        try:
            scratch_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise GitCorpusError(
                f"could not create {scratch_root}: {exc.strerror or exc}", system="local"
            ) from exc
        with tempfile.TemporaryDirectory(prefix=".validate-", dir=scratch_root) as scratch:
            # --no-index needs no repository, so validation costs one git call.
            args = ["grep", "--no-index", "-n"]
            args.append("--fixed-strings" if fixed_strings else "--extended-regexp")
            args.extend(["-e", pattern, "--"])
            args.extend(paths)
            # No store and no remote: the one direct ``run_git`` call.
            try:
                result = run_git(
                    args,
                    cwd=Path(scratch),
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

    def list_repos(self) -> tuple[CorpusRepoResult, ...]:
        """List the store repos github has synced, sorted by name."""
        rows = [
            CorpusRepoResult(
                full_name=str(data.get("repo") or ""),
                ref=str(data.get("ref") or ""),
                path=str(store.path),
                clone_url=_optional_str(data.get("clone_url")),
                status="cached",
                fetched_at=_optional_str(data.get("fetched_at")),
                profile=_metadata_profile(data),
                ref_globs=_metadata_ref_globs(data),
                archived=_metadata_archived(data),
            )
            for store, data in self._entries()
        ]
        # By name: host-less repos (`file://`, local paths) are keyed by a hash.
        return tuple(sorted((row for row in rows if row.full_name and row.ref), key=_by_name))

    def get_repo(self, repo: str) -> CorpusRepoTarget | None:
        """Return the stored metadata of ``repo`` if github has synced it."""
        for _store, data in self._entries():
            if data.get("repo") != repo:
                continue
            return CorpusRepoTarget(
                full_name=repo,
                default_branch=_optional_str(data.get("ref")),
                clone_url=_optional_str(data.get("clone_url")),
                archived=_metadata_archived(data),
            )
        return None

    def _entries(self) -> Iterator[tuple[RepoStore, dict[str, object]]]:
        """Each store repo with github's metadata, warning on and skipping corrupt files.

        A repo not at ``store_key`` of its recorded ``clone_url`` (moved by
        hand, say) is not listed: no command could use it.
        """
        for store in RepoStore.owned_by(SPEC, error=GitCorpusError):
            try:
                data = _read_metadata(store.private_file)
            except GitCorpusError as exc:
                self._warn(str(exc))
                continue
            clone_url = _optional_str(data.get("clone_url"))
            if clone_url is None:
                continue
            key = store_key(clone_url)
            if store.path.parts[-len(key) :] == key:
                yield store, data

    def clean_repo(self, repo: CorpusRepoResult) -> CorpusRepoResult:
        """Release one repo: it goes when nobody else uses it, else github's part of it goes.

        The row says ``removed`` with the bytes freed in ``disk_bytes``, or
        ``released`` with who kept the repo in ``kept``.
        """
        if repo.clone_url is None:
            raise GitCorpusError(f"{repo.full_name} has no clone_url in its corpus metadata")
        outcome = self._store(repo.clone_url).release()
        if isinstance(outcome, Removed):
            return repo.model_copy(
                update={"status": "removed", "kept": None, "disk_bytes": outcome.freed_bytes}
            )
        return repo.model_copy(
            update={"status": "released", "kept": outcome.kept(), "disk_bytes": 0}
        )

    def materialize_worktree(self, repo: CorpusRepoTarget, *, ref: str | None) -> WorktreeResult:
        """Check one stored ref out in a worktree github owns, detached."""
        branch = _default_branch(repo)
        selected_ref = ref or branch
        store = self._existing(repo)
        target = _resolve_ref(store, ref or f"refs/heads/{branch}")
        if target is None:
            raise GitCorpusError(
                f"ref is not cached: {selected_ref}; run a sweep that fetches it",
                category="not_found",
            )
        worktree = _worktree_path(repo.full_name, selected_ref)
        if worktree.exists() and not (worktree / ".git").exists():
            raise GitCorpusError(f"worktree path exists and is not a git worktree: {worktree}")
        if worktree.exists():
            store.checkout(worktree, target)
        else:
            try:
                worktree.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise GitCorpusError(
                    f"could not create {worktree.parent}: {exc.strerror or exc}", system="local"
                ) from exc
            store.worktree_add(worktree, target)
        return WorktreeResult(
            target_path=worktree.absolute(),
            path=str(worktree),
            repo=repo.full_name,
            ref=selected_ref,
        )

    def _store(self, url: str) -> RepoStore:
        return RepoStore.for_url(url, plugin=SPEC, error=GitCorpusError)

    def _existing(self, repo: CorpusRepoTarget) -> RepoStore:
        """The store repo of ``repo``; ``not_found`` when it has never been synced."""
        store = self._store(self._remote_url(repo))
        if not store.exists():
            raise GitCorpusError("repository is not in the local corpus", category="not_found")
        return store

    def _remote_url(self, repo: CorpusRepoTarget) -> str:
        """The URL github fetches ``repo`` from: its clone URL, in ``protocol`` on GitHub."""
        if repo.clone_url:
            url = repo.clone_url
        elif repo.html_url:
            url = f"{repo.html_url.removesuffix('/')}.git"
        else:
            url = f"https://github.com/{repo.full_name}.git"
        if self._protocol == "ssh" and self._web_host and _https_host(url) == self._web_host:
            return f"git@{self._web_host}:{repo.full_name}.git"
        return url

    def _ref_names(
        self, url: str, selector: RefSelector, *, default_branch: str
    ) -> tuple[list[str], list[str]]:
        """The store's branch and tag names (or one-``*`` globs) that ``selector`` covers.

        A ``--ref`` glob the store cannot take (``?``, ``[``, two ``*``) is
        matched against the remote's refs once, here, and its matches passed
        by name.
        """
        branches = ["*"] if selector.profile in {"branches", "all"} else [default_branch]
        tags = ["*"] if selector.profile in {"tags", "all"} else []
        listed = []
        for glob in selector.globs:
            if _STORE_GLOB.fullmatch(glob):
                branches.append(glob)
                tags.append(glob)
            else:
                listed.append(glob)
        if listed:
            for ref in self._remote_refs(url):
                kind, _, name = ref.removeprefix("refs/").partition("/")
                if any(fnmatch.fnmatchcase(name, glob) for glob in listed):
                    (branches if kind == "heads" else tags).append(name)
        return _names(branches), _names(tags)

    def _remote_refs(self, url: str) -> list[str]:
        try:
            refs = ls_remote(url, ["refs/heads/*", "refs/tags/*"])
        except UntapedError as exc:
            raise GitCorpusError(str(exc), **attribution(exc)) from exc
        return sorted(ref for ref in refs if ref.startswith(_PLAIN_ROOTS))


def prefetch_paths(pathspecs: Sequence[str]) -> tuple[str, ...]:
    """The paths a prefetch for a grep over ``pathspecs`` covers; empty is the whole tree.

    ``rev-list --objects -- <pathspec>`` does not match a wildcard across
    directories as ``git grep`` does, so each include widens to a plain
    directory prefix: the part before its first wildcard, up to the last
    ``/``. An ``exclude`` pathspec only narrows the grep and is left out;
    any other magic, or a wildcard with no directory before it, means the
    whole tree.
    """
    paths: list[str] = []
    for spec in pathspecs:
        if spec.startswith((":!", ":^")) or re.match(r":\((?:[^)]*,)?exclude\b", spec):
            continue
        if spec.startswith(":"):
            return ()
        wildcard = _WILDCARD.search(spec)
        if wildcard is None:
            paths.append(spec)
            continue
        prefix = spec[: wildcard.start()].rpartition("/")[0]
        if not prefix:
            return ()
        paths.append(f"{prefix}/")
    return tuple(dict.fromkeys(paths))


def _names(names: list[str]) -> list[str]:
    """``names`` deduplicated; a ``*`` alone covers the rest of its list."""
    return ["*"] if "*" in names else list(dict.fromkeys(names))


def _freshness(store: RepoStore) -> CorpusFreshness | None:
    metadata_path = store.private_file
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


def _resolve_ref(store: RepoStore, ref: str) -> str | None:
    """The stored ref (or commit) a user's ``--ref`` names, in git's order: tags, then heads.

    Takes ``main``, ``refs/heads/main``, ``heads/main``, the same for tags,
    or a full commit id the repo has.
    """
    refs = store.refs()
    relative = ref.removeprefix("refs/")
    if relative.startswith(("heads/", "tags/")):
        candidates = [relative]
    else:
        candidates = [f"tags/{ref}", f"heads/{ref}"]
    for candidate in candidates:
        if candidate in refs:
            return store.ref(candidate)
    if _OID.fullmatch(ref):
        verify = ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]
        if store.run(verify, check=False).returncode == 0:
            return ref
    return None


def _https_host(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.hostname.lower() if parsed.scheme == "https" and parsed.hostname else None


def _ui_warning(message: str) -> None:
    ui_context(strict=False).message("warning", message)


def _default_branch(repo: CorpusRepoTarget) -> str:
    if not repo.default_branch:
        raise GitCorpusError(f"repository metadata missing default_branch: {repo.full_name}")
    return repo.default_branch


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


def _worktree_path(repo: str, ref: str) -> Path:
    digest = hashlib.sha256(f"{repo}@{ref}".encode()).hexdigest()[:12]
    name = f"{safe_path_segment(repo)}-{safe_path_segment(ref)}-{digest}"
    return plugin_dir(SPEC) / "worktrees" / name


def _write_metadata(store: RepoStore, data: dict[str, object]) -> None:
    atomic_write(store.private_file, json.dumps(data, sort_keys=True) + "\n")


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
    return (row.full_name, row.path)
