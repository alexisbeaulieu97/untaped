"""Bare Git cache used by dependency source indexing."""

from __future__ import annotations

from pathlib import Path

from untaped.capabilities.ansible.errors import GitCacheError as GitCacheError
from untaped.sdk import (
    GitCommandError,
    RepoCache,
    attribution,
    cache_path,
    git_toplevel,
    run_git,
    scoped_auth_header,
)

DEFAULT_TIMEOUT = 60.0
DEFAULT_SLOW_TIMEOUT = 600.0
# ``cat-file --batch`` reads many blobs in one process: allow extra time per blob.
PER_FILE_READ_TIMEOUT = 1.0


class GitRepositoryCache:
    """Maintain bare repositories and read dependency files from Git objects."""

    def __init__(
        self,
        *,
        auth_host: str | None,
        git: str = "git",
        timeout: float = DEFAULT_TIMEOUT,
        slow_timeout: float = DEFAULT_SLOW_TIMEOUT,
        lock_timeout: float = 600.0,
    ) -> None:
        self._auth_host = auth_host
        self._git = git
        self._timeout = timeout
        self._slow_timeout = slow_timeout
        self._lock_timeout = lock_timeout
        # The url each ``ensure_bare`` set, so ``fetch_refs`` re-points a cache
        # that another process (the other url form of a shared cache) changed.
        self._urls: dict[Path, str] = {}

    def _cache(self, path: Path, auth_header: str | None) -> RepoCache:
        # A handle per call: the token may differ, and the origin is re-read.
        return RepoCache(
            path,
            error=GitCacheError,
            auth_header=auth_header,
            auth_host=self._auth_host,
            git=self._git,
            timeout=self._timeout,
            slow_timeout=self._slow_timeout,
            lock_timeout=self._lock_timeout,
        )

    def ensure_bare(
        self,
        url: str,
        *,
        cache_dir: Path,
        auth_header: str | None,
    ) -> Path:
        """Ensure a bare repository cache exists for ``url``."""
        cache = self._cache(cache_path(url, root=cache_dir), auth_header)
        with cache.locked():
            cache.ensure(url)
        self._urls[cache.path] = url
        return cache.path

    def fetch_refs(
        self,
        bare_path: Path,
        *,
        refspecs: list[str],
        depth: int,
        blob_filter: bool,
        auth_header: str | None,
    ) -> None:
        """Fetch selected refs into a bare cache.

        When ``ensure_bare`` set this cache's url, it is re-applied under the
        same lock as the fetch: https and ssh share a cache, so a concurrent
        refresh may have repointed ``origin`` in between.
        """
        if not refspecs:
            return
        cache = self._cache(bare_path, auth_header)
        url = self._urls.get(bare_path)
        with cache.locked():
            if url is not None:
                cache.ensure(url)
            cache.fetch(refspecs, depth=depth, filter="blob:none" if blob_filter else None)

    def ls_remote(
        self,
        url: str,
        *,
        patterns: list[str],
        auth_header: str | None,
    ) -> str:
        """Run ``git ls-remote --symref`` without requiring a local repository."""
        header = scoped_auth_header(url, auth_header, host=self._auth_host)
        try:
            result = run_git(
                ["ls-remote", "--symref", url, *patterns],
                git=self._git,
                timeout=self._timeout,
                capture=True,
                auth_header=header,
                auth_url=url if header is not None else None,
            )
        except GitCommandError as exc:
            raise GitCacheError(str(exc), **attribution(exc)) from exc
        return result.text

    def read_files(
        self,
        bare_path: Path,
        sha: str,
        paths: list[str],
        *,
        auth_header: str | None,
    ) -> dict[str, str]:
        """Read the blobs among ``paths`` that exist at ``sha``.

        One ``ls-tree`` lists which paths exist as blobs, then one
        ``cat-file --batch`` reads them all. Absent paths are simply omitted:
        existence comes from the listing, never from (possibly translated)
        Git error text, so any non-zero exit is a real failure.
        """
        wanted = list(dict.fromkeys(paths))
        if not wanted:
            return {}
        cache = self._cache(bare_path, auth_header)
        listing = cache.run(["ls-tree", "-z", sha, "--", *wanted], capture=True).text
        blob_by_path: dict[str, str] = {}
        for entry in listing.split("\0"):
            meta, separator, entry_path = entry.partition("\t")
            fields = meta.split()
            if separator and len(fields) == 3 and fields[1] == "blob" and entry_path in wanted:
                blob_by_path[entry_path] = fields[2]
        if not blob_by_path:
            return {}
        blob_ids = list(dict.fromkeys(blob_by_path.values()))
        # Blob-filtered caches fetch missing blobs lazily from origin.
        result = cache.run(
            ["cat-file", "--batch"],
            capture=True,
            stdin="".join(f"{blob}\n" for blob in blob_ids).encode(),
            timeout=self._timeout + PER_FILE_READ_TIMEOUT * len(blob_ids),
        )
        contents = _parse_cat_file_batch(result.stdout, blob_ids)
        return {path: contents[blob] for path, blob in blob_by_path.items()}


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
