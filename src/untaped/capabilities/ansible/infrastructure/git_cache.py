"""Bare Git cache used by dependency source indexing."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from untaped.capabilities.ansible.errors import GitCacheError as GitCacheError

DEFAULT_TIMEOUT = 60.0
DEFAULT_SLOW_TIMEOUT = 600.0


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
        self._git_path = shutil.which(git)
        self._timeout = timeout
        self._slow_timeout = slow_timeout

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
        self._ensure_origin(bare, url, auth_header=auth_header)
        return bare

    def _ensure_origin(self, bare: Path, url: str, *, auth_header: str | None) -> None:
        current_url = self._run(
            ["remote", "get-url", "origin"],
            cwd=bare,
            capture=True,
            check=False,
            auth_header=auth_header,
        ).strip()
        if not current_url:
            self._run(["remote", "add", "origin", url], cwd=bare, auth_header=auth_header)
            return
        if current_url != url:
            self._run(["remote", "set-url", "origin", url], cwd=bare, auth_header=auth_header)

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
        self._run(args, cwd=bare_path, timeout=self._slow_timeout, auth_header=auth_header)

    def ls_remote(
        self,
        url: str,
        *,
        patterns: list[str],
        auth_header: str | None,
    ) -> str:
        """Run ``git ls-remote --symref`` without requiring a local repository."""
        return self._run(
            ["ls-remote", "--symref", url, *patterns],
            capture=True,
            auth_header=auth_header,
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
            auth_header=auth_header,
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
        output = self._run_bytes(
            ["cat-file", "--batch"],
            cwd=bare_path,
            stdin_data="".join(f"{blob}\n" for blob in blob_ids).encode(),
            auth_header=auth_header,
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
    ) -> str:
        result = self._exec(
            args,
            cwd=cwd,
            timeout=timeout,
            auth_header=auth_header,
            stdin_data=None,
        )
        if check and result.returncode != 0:
            raise _command_error(args, result.stderr, auth_header)
        stdout = result.stdout
        return stdout if capture and isinstance(stdout, str) else ""

    def _run_bytes(
        self,
        args: list[str],
        *,
        cwd: Path,
        stdin_data: bytes,
        auth_header: str | None,
    ) -> bytes:
        result = self._exec(
            args,
            cwd=cwd,
            timeout=None,
            auth_header=auth_header,
            stdin_data=stdin_data,
        )
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        if result.returncode != 0:
            raise _command_error(args, stderr, auth_header)
        stdout = result.stdout
        return stdout if isinstance(stdout, bytes) else stdout.encode()

    def _exec(
        self,
        args: list[str],
        *,
        cwd: Path | None,
        timeout: float | None,
        auth_header: str | None,
        stdin_data: bytes | None,
    ) -> subprocess.CompletedProcess[Any]:
        if self._git_path is None:
            raise GitCacheError(f"`{self._git}` not found on PATH")
        effective_timeout = self._timeout if timeout is None else timeout
        cmd = [self._git_path, *args]
        auth_config_path: Path | None = None
        if auth_header is not None:
            env, auth_config_path = _auth_config_env(auth_header)
        else:
            env = _git_env()
        try:
            if stdin_data is None:
                return subprocess.run(
                    cmd,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=effective_timeout,
                )
            return subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                input=stdin_data,
                capture_output=True,
                check=False,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitCacheError(
                f"git {' '.join(args)} timed out after {effective_timeout:g}s"
            ) from exc
        finally:
            if auth_config_path is not None:
                auth_config_path.unlink(missing_ok=True)


def local_remote_url(
    path: Path, *, git: str = "git", timeout: float = DEFAULT_TIMEOUT
) -> str | None:
    """Return the ``origin`` remote URL (else the first remote URL) for ``path``.

    Delegates to ``git config`` so linked worktrees, config includes, and
    repeated keys resolve exactly as Git itself resolves them. Returns
    ``None`` unless ``path`` is the top level of a checkout with a remote: a
    subdirectory (say ``roles/web`` in a monorepo) is not the enclosing repo,
    whose indexed edges its local overlay would otherwise replace. Inherited
    ``GIT_DIR``/``GIT_WORK_TREE``/``GIT_INDEX_FILE`` are dropped so the
    lookup always targets ``path``.
    """
    git_path = shutil.which(git)
    if git_path is None:
        return None
    cwd = path if path.is_dir() else path.parent
    env = _git_env()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    try:
        toplevel = subprocess.run(
            [git_path, "-C", str(cwd), "rev-parse", "--show-toplevel"],
            env=env,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    top = toplevel.stdout.strip() if toplevel.returncode == 0 else ""
    if not top or Path(top).resolve() != cwd.resolve():
        return None
    lookups = (
        ["config", "--get", "remote.origin.url"],
        ["config", "--get-regexp", r"^remote\..*\.url$"],
    )
    for args in lookups:
        try:
            result = subprocess.run(
                [git_path, "-C", str(cwd), *args],
                env=env,
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except OSError, subprocess.TimeoutExpired:
            return None
        lines = result.stdout.strip().splitlines() if result.returncode == 0 else []
        if lines:
            value = lines[0] if args[1] == "--get" else lines[0].split(maxsplit=1)[-1]
            return value.strip() or None
    return None


def cache_path_for(url: str, *, cache_dir: Path) -> Path:
    """Return the deterministic bare-cache path for a remote URL."""
    parsed = urlparse(url)
    if parsed.scheme and parsed.path:
        base_name = Path(parsed.path.rstrip("/")).name
        host = parsed.netloc or "local"
    elif ":" in url and "@" in url.split(":", maxsplit=1)[0]:
        host_part, _, path_part = url.partition(":")
        host = host_part.rsplit("@", maxsplit=1)[-1]
        base_name = Path(path_part.rstrip("/")).name
    else:
        host = "local"
        base_name = Path(url.rstrip("/")).name
    if not base_name:
        base_name = "repository"
    if not base_name.endswith(".git"):
        base_name = f"{base_name}.git"
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    safe_host = _safe_path_part(host)
    safe_name = _safe_path_part(base_name.removesuffix(".git"))
    return cache_dir.expanduser() / safe_host / f"{safe_name}-{digest}.git"


def _safe_path_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def _git_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for every Git subprocess: C locale, never prompt."""
    env = dict(os.environ if base is None else base)
    env["LC_ALL"] = "C"
    env["LANGUAGE"] = "C"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    return env


def _auth_config_env(auth_header: str) -> tuple[dict[str, str], Path]:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="untaped-git-auth-",
        suffix=".config",
        delete=False,
    ) as auth_config:
        auth_config.write("[http]\n")
        auth_config.write(f"\textraheader = {auth_header}\n")
        path = Path(auth_config.name)
    env = _git_env()
    count = _git_config_count(env)
    env[f"GIT_CONFIG_KEY_{count}"] = "include.path"
    env[f"GIT_CONFIG_VALUE_{count}"] = str(path)
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    return env, path


def _git_config_count(env: dict[str, str]) -> int:
    raw = env.get("GIT_CONFIG_COUNT")
    if raw is None:
        return 0
    try:
        count = int(raw)
    except ValueError:
        return 0
    return max(count, 0)


def _redact(value: str, secret: str | None) -> str:
    if secret is None:
        return value
    return value.replace(secret, "<redacted>")


def _command_error(args: list[str], stderr: str | None, auth_header: str | None) -> GitCacheError:
    message = _redact((stderr or "").strip(), auth_header)
    return GitCacheError(f"git {' '.join(args)} failed: {message or 'no stderr'}")


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
        contents[fields[0]] = output[start : start + size].decode("utf-8", errors="replace")
        offset = start + size + 1
    return contents
