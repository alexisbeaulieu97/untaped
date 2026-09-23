"""Bare Git cache used by dependency source indexing."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from untaped.capabilities.ansible.errors import GitCacheError as GitCacheError
from untaped.capability_api import GitCommandError, GitResult, run_git, safe_cache_path

DEFAULT_TIMEOUT = 60.0
DEFAULT_SLOW_TIMEOUT = 600.0
# ``cat-file --batch`` reads many blobs in one process: allow extra time per blob.
PER_FILE_READ_TIMEOUT = 1.0


class GitRepositoryCache:
    """Maintain bare repositories and read dependency files from Git objects."""

    def __init__(
        self,
        *,
        git: str = "git",
        timeout: float = DEFAULT_TIMEOUT,
        slow_timeout: float = DEFAULT_SLOW_TIMEOUT,
    ) -> None:
        self._git = git
        self._timeout = timeout
        self._slow_timeout = slow_timeout
        self._origins: dict[Path, str] = {}

    def ensure_bare(
        self,
        url: str,
        *,
        cache_dir: Path,
        auth_header: str | None,
    ) -> Path:
        """Ensure a bare repository cache exists for ``url``."""
        bare = cache_path_for(url, cache_dir=cache_dir)
        if not (bare / "HEAD").is_file():
            bare.parent.mkdir(parents=True, exist_ok=True)
            self._run(["init", "--bare", str(bare)], timeout=self._slow_timeout)
        self._ensure_origin(bare, url)
        return bare

    def _ensure_origin(self, bare: Path, url: str) -> None:
        # Purely local commands: the auth header is never needed here.
        current_url = self._run(
            ["remote", "get-url", "origin"], cwd=bare, capture=True, check=False
        ).strip()
        if not current_url:
            self._run(["remote", "add", "origin", url], cwd=bare)
        elif current_url != url:
            self._run(["remote", "set-url", "origin", url], cwd=bare)
        self._origins[bare] = url

    def _origin_auth(self, bare: Path, auth_header: str | None) -> tuple[str | None, str | None]:
        if auth_header is None:
            return None, None
        return _scoped_auth(auth_header, self._origin_url(bare))

    def _origin_url(self, bare: Path) -> str:
        if bare not in self._origins:
            self._origins[bare] = self._run(
                ["remote", "get-url", "origin"], cwd=bare, capture=True, check=False
            ).strip()
        return self._origins[bare]

    def fetch_refs(
        self,
        bare_path: Path,
        *,
        refspecs: list[str],
        depth: int,
        blob_filter: bool,
        auth_header: str | None,
    ) -> None:
        """Fetch selected refs into a bare cache."""
        if not refspecs:
            return
        args = ["fetch", "--prune", "origin"]
        if depth > 0:
            args.append(f"--depth={depth}")
        if blob_filter:
            args.append("--filter=blob:none")
        args.extend(refspecs)
        header, auth_url = self._origin_auth(bare_path, auth_header)
        self._run(
            args,
            cwd=bare_path,
            timeout=self._slow_timeout,
            auth_header=header,
            auth_url=auth_url,
        )

    def ls_remote(
        self,
        url: str,
        *,
        patterns: list[str],
        auth_header: str | None,
    ) -> str:
        """Run ``git ls-remote --symref`` without requiring a local repository."""
        header, auth_url = _scoped_auth(auth_header, url)
        return self._run(
            ["ls-remote", "--symref", url, *patterns],
            capture=True,
            auth_header=header,
            auth_url=auth_url,
        )

    def read_file(
        self,
        bare_path: Path,
        sha: str,
        path: str,
        *,
        auth_header: str | None,
    ) -> str | None:
        """Read ``path`` from ``sha`` without checking out a worktree."""
        return self.read_files(bare_path, sha, [path], auth_header=auth_header).get(path)

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
        listing = self._run(
            ["ls-tree", "-z", sha, "--", *wanted],
            cwd=bare_path,
            capture=True,
        )
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
        header, auth_url = self._origin_auth(bare_path, auth_header)
        output = self._run_bytes(
            ["cat-file", "--batch"],
            cwd=bare_path,
            stdin_data="".join(f"{blob}\n" for blob in blob_ids).encode(),
            auth_header=header,
            auth_url=auth_url,
            timeout=self._timeout + PER_FILE_READ_TIMEOUT * len(blob_ids),
        )
        contents = _parse_cat_file_batch(output, blob_ids)
        return {path: contents[blob] for path, blob in blob_by_path.items()}

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        capture: bool = False,
        check: bool = True,
        timeout: float | None = None,
        auth_header: str | None = None,
        auth_url: str | None = None,
    ) -> str:
        result = self._exec(
            args,
            cwd=cwd,
            timeout=timeout,
            auth_header=auth_header,
            auth_url=auth_url,
            check=check,
        )
        return result.text if capture else ""

    def _run_bytes(
        self,
        args: list[str],
        *,
        cwd: Path,
        stdin_data: bytes,
        auth_header: str | None = None,
        auth_url: str | None = None,
        timeout: float | None = None,
    ) -> bytes:
        return self._exec(
            args,
            cwd=cwd,
            timeout=timeout,
            auth_header=auth_header,
            auth_url=auth_url,
            stdin_data=stdin_data,
        ).stdout

    def _exec(
        self,
        args: list[str],
        *,
        cwd: Path | None,
        timeout: float | None,
        auth_header: str | None,
        auth_url: str | None = None,
        stdin_data: bytes | None = None,
        check: bool = True,
    ) -> GitResult:
        try:
            return run_git(
                args,
                cwd=cwd,
                git=self._git,
                timeout=self._timeout if timeout is None else timeout,
                capture=True,
                stdin=stdin_data,
                check=check,
                auth_header=auth_header,
                auth_url=auth_url,
            )
        except GitCommandError as exc:
            raise GitCacheError(str(exc)) from exc


def _scoped_auth(auth_header: str | None, url: str) -> tuple[str | None, str | None]:
    """Send the token only to the repository's own HTTPS origin, never elsewhere."""
    parsed = urlparse(url)
    if auth_header is None or parsed.scheme != "https" or not parsed.netloc:
        return None, None
    return auth_header, url


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

    top = lookup("rev-parse", "--show-toplevel")
    if not top or Path(top).resolve() != cwd.resolve():
        return None
    lines = lookup("config", "--get", "remote.origin.url").splitlines()
    if lines:
        return lines[0].strip() or None
    lines = lookup("config", "--get-regexp", r"^remote\..*\.url$").splitlines()
    if lines:
        return lines[0].split(maxsplit=1)[-1].strip() or None
    return None


def cache_path_for(url: str, *, cache_dir: Path) -> Path:
    """Return the deterministic bare-cache path for a remote URL."""
    return safe_cache_path(url, root=cache_dir)


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
