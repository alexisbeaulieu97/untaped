"""Local bare Git corpus adapter for sweep and cache commands."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

from untaped.capabilities.github.domain import (
    CorpusFreshness,
    CorpusRepoResult,
    CorpusRepoTarget,
    GrepHit,
    RefProfile,
    RefSelector,
    WorktreeResult,
    profile_join,
)
from untaped.capabilities.github.domain.errors import GitCorpusError

DEFAULT_TIMEOUT = 60.0
DEFAULT_SLOW_TIMEOUT = 600.0
METADATA_FILE = "untaped-corpus.json"


class GitCorpusCache:
    """Maintain a managed bare Git corpus and search it with ``git grep``."""

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
        branch = _default_branch(repo)
        url = _remote_url(repo)
        scoped_auth_header = _auth_header_for_url(url, auth_header)
        bare = cache_path_for(url, cache_dir=root)
        if not (bare / "HEAD").is_file():
            bare.parent.mkdir(parents=True, exist_ok=True)
            self._run(["init", "--bare", str(bare)], timeout=self._slow_timeout)
        self._ensure_origin(bare, url, auth_header=scoped_auth_header)

        stored = self.repo_freshness(repo, root=root)
        profile = profile_join(stored.profile, selector.profile) if stored else selector.profile
        ref_globs = _join_globs(stored.ref_globs if stored else (), selector.globs)
        effective = RefSelector(profile=profile, globs=ref_globs)

        self._fetch_refspecs(
            bare,
            url=url,
            depth=depth,
            auth_header=scoped_auth_header,
            refspecs=_profile_refspecs(effective.profile, branch),
        )
        for glob in effective.globs:
            for namespace in ("heads", "tags"):
                self._fetch_optional_refspec(
                    bare,
                    url=url,
                    depth=depth,
                    auth_header=scoped_auth_header,
                    refspec=f"+refs/{namespace}/{glob}:refs/{namespace}/{glob}",
                )
        self._prune_uncovered_refs(bare, selector=effective, default_branch=branch)

        fetched_at = datetime.now(UTC).isoformat()
        _write_metadata(
            bare,
            {
                "repo": repo.full_name,
                "ref": branch,
                "clone_url": url,
                "fetched_at": fetched_at,
                "profile": effective.profile,
                "ref_globs": list(effective.globs),
                "archived": repo.archived,
            },
        )
        return CorpusRepoResult(
            repo=repo.full_name,
            ref=branch,
            path=str(bare),
            clone_url=url,
            status="synced",
            fetched_at=fetched_at,
            profile=effective.profile,
            ref_globs=effective.globs,
            archived=repo.archived,
        )

    def repo_freshness(self, repo: CorpusRepoTarget, *, root: Path) -> CorpusFreshness | None:
        """Return cached fetch metadata for ``repo`` if present."""
        metadata_path = cache_path_for(_remote_url(repo), cache_dir=root) / METADATA_FILE
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
        )

    def local_refs(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        selector: RefSelector,
    ) -> tuple[str, ...]:
        """Return cached refs selected for a sweep, with default branch first."""
        branch = _default_branch(repo)
        bare = cache_path_for(_remote_url(repo), cache_dir=root)
        if not (bare / "HEAD").is_file():
            return ()
        result = cast(
            subprocess.CompletedProcess[str],
            self._run(
                ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags"],
                cwd=bare,
                capture_text=True,
            ),
        )
        refs = (
            _short_ref(ref)
            for ref in (result.stdout or "").splitlines()
            if _selector_covers_ref(selector, ref, default_branch=branch)
        )
        return _order_refs(tuple(dict.fromkeys(refs)), default_branch=branch)

    def grep_ref(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        ref: str,
        pattern: str,
        paths: tuple[str, ...],
        ignore_case: bool,
        fixed_strings: bool,
        word_regexp: bool,
    ) -> tuple[GrepHit, ...]:
        """Run ``git grep`` against one cached ref and include blob OIDs."""
        bare = _cached_bare(repo, root=root)
        args = ["grep", "-n", "--column", "-z", "-I"]
        if ignore_case:
            args.append("--ignore-case")
        if fixed_strings:
            args.append("--fixed-strings")
        if word_regexp:
            args.append("--word-regexp")
        args.extend(["-e", pattern, ref, "--"])
        args.extend(paths)
        result = cast(
            subprocess.CompletedProcess[bytes],
            self._run(args, cwd=bare, capture_bytes=True, check=False),
        )
        if result.returncode == 1:
            return ()
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode(errors="replace").strip()
            raise GitCorpusError(stderr or f"git grep failed with status {result.returncode}")

        oids: dict[str, str] = {}
        hits: list[GrepHit] = []
        for path, line, text in _parse_ref_grep_output(result.stdout or b"", ref=ref):
            oid = oids.get(path)
            if oid is None:
                oid = _blob_oid(self, bare, ref=ref, path=path)
                oids[path] = oid
            hits.append(GrepHit(path=path, line=line, text=text, blob_oid=oid))
        return tuple(hits)

    def tree_paths(self, repo: CorpusRepoTarget, *, root: Path, ref: str) -> tuple[str, ...]:
        """List paths in one cached ref tree."""
        bare = _cached_bare(repo, root=root)
        result = cast(
            subprocess.CompletedProcess[bytes],
            self._run(["ls-tree", "-r", "--name-only", "-z", ref], cwd=bare, capture_bytes=True),
        )
        return tuple(
            part.decode(errors="replace") for part in (result.stdout or b"").split(b"\0") if part
        )

    def read_blob(
        self,
        repo: CorpusRepoTarget,
        *,
        root: Path,
        ref: str,
        path: str,
    ) -> str | None:
        """Read a text blob from one cached ref, returning None when absent."""
        bare = _cached_bare(repo, root=root)
        result = cast(
            subprocess.CompletedProcess[bytes],
            self._run(["show", f"{ref}:{path}"], cwd=bare, capture_bytes=True, check=False),
        )
        if result.returncode != 0:
            return None
        return (result.stdout or b"").decode(errors="replace")

    def validate_pattern(
        self,
        *,
        root: Path,
        pattern: str,
        paths: tuple[str, ...],
        fixed_strings: bool,
    ) -> str | None:
        """Validate one grep pattern and its pathspecs in a scratch repository."""
        managed_root = root.expanduser()
        managed_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".validate-", dir=managed_root) as scratch:
            scratch_path = Path(scratch)
            self._run(["init", "-q"], cwd=scratch_path)
            args = ["grep", "-n"]
            if fixed_strings:
                args.append("--fixed-strings")
            args.extend(["-e", pattern, "--"])
            args.extend(paths)
            result = cast(
                subprocess.CompletedProcess[str],
                self._run(args, cwd=scratch_path, capture_text=True, check=False),
            )
        if result.returncode in {0, 1}:
            return None
        return _stderr_text(result)

    def list_repos(self, *, root: Path) -> tuple[CorpusRepoResult, ...]:
        """List repositories with corpus metadata under ``root``."""
        managed_root = root.expanduser()
        if not managed_root.exists():
            return ()
        rows: list[CorpusRepoResult] = []
        for metadata_path in sorted(managed_root.rglob(METADATA_FILE)):
            bare = metadata_path.parent
            try:
                data = _read_metadata(metadata_path)
            except GitCorpusError as exc:
                print(f"warning: {exc}", file=sys.stderr)
                continue
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
        return tuple(row for row in rows if row.repo and row.ref)

    def get_repo(self, *, root: Path, repo: str) -> CorpusRepoTarget | None:
        """Return cached repository metadata for ``repo`` if present."""
        managed_root = root.expanduser()
        if not managed_root.exists():
            return None
        for metadata_path in sorted(managed_root.rglob(METADATA_FILE)):
            data = _read_metadata(metadata_path)
            if data.get("repo") != repo:
                continue
            return CorpusRepoTarget(
                full_name=repo,
                default_branch=_optional_str(data.get("ref")),
                clone_url=_optional_str(data.get("clone_url")),
                archived=_metadata_archived(data),
            )
        return None

    def clean_repo(self, *, root: Path, repo: CorpusRepoResult) -> CorpusRepoResult:
        """Remove one cached repository from the managed corpus root."""
        managed_root = root.expanduser().resolve()
        bare = Path(repo.path).expanduser().resolve()
        if not bare.is_relative_to(managed_root):
            raise GitCorpusError(f"refusing to remove path outside managed root: {bare}")
        self._remove_managed_worktrees(bare, managed_root=managed_root)
        shutil.rmtree(bare)
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
        bare = cache_path_for(_remote_url(repo), cache_dir=root)
        if not (bare / "HEAD").is_file():
            raise GitCorpusError("repository is not in the local corpus")
        if not self._ref_exists(bare, selected_ref):
            raise GitCorpusError(f"ref is not cached: {selected_ref}; run a sweep that fetches it")
        worktree = _worktree_path(repo.full_name, selected_ref, root=root)
        if worktree.exists() and not (worktree / ".git").exists():
            raise GitCorpusError(f"worktree path exists and is not a git worktree: {worktree}")
        if worktree.exists():
            self._run(
                ["checkout", "--detach", selected_ref], cwd=worktree, timeout=self._slow_timeout
            )
        else:
            worktree.parent.mkdir(parents=True, exist_ok=True)
            self._run(
                ["worktree", "add", "--detach", str(worktree), selected_ref],
                cwd=bare,
                timeout=self._slow_timeout,
            )
        return WorktreeResult(repo=repo.full_name, ref=selected_ref, path=str(worktree))

    def _ensure_origin(self, bare: Path, url: str, *, auth_header: str | None) -> None:
        current = self._run(
            ["remote", "get-url", "origin"],
            cwd=bare,
            capture_text=True,
            check=False,
            auth_header=auth_header,
            auth_url=url,
        )
        current_url = (current.stdout or "").strip()
        if not current_url:
            self._run(
                ["remote", "add", "origin", url], cwd=bare, auth_header=auth_header, auth_url=url
            )
        elif current_url != url:
            self._run(
                ["remote", "set-url", "origin", url],
                cwd=bare,
                auth_header=auth_header,
                auth_url=url,
            )

    def _fetch_refspecs(
        self,
        bare: Path,
        *,
        url: str,
        depth: int,
        auth_header: str | None,
        refspecs: tuple[str, ...],
    ) -> None:
        args = ["fetch", "--prune", "--no-tags", "origin"]
        if depth > 0:
            args.append(f"--depth={depth}")
        args.extend(refspecs)
        self._run(
            args,
            cwd=bare,
            timeout=self._slow_timeout,
            auth_header=auth_header,
            auth_url=url,
        )

    def _fetch_optional_refspec(
        self,
        bare: Path,
        *,
        url: str,
        depth: int,
        auth_header: str | None,
        refspec: str,
    ) -> None:
        args = ["fetch", "--prune", "origin"]
        if depth > 0:
            args.append(f"--depth={depth}")
        args.append(refspec)
        result = self._run(
            args,
            cwd=bare,
            timeout=self._slow_timeout,
            auth_header=auth_header,
            auth_url=url,
            capture_text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        stderr = _stderr_text(result)
        if not stderr or "couldn't find remote ref" in stderr:
            return
        raise GitCorpusError(
            f"git {' '.join(args)} failed: {_redact(stderr, auth_header) or 'no stderr'}"
        )

    def _prune_uncovered_refs(
        self,
        bare: Path,
        *,
        selector: RefSelector,
        default_branch: str,
    ) -> None:
        result = cast(
            subprocess.CompletedProcess[str],
            self._run(
                ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags"],
                cwd=bare,
                capture_text=True,
            ),
        )
        for ref in (result.stdout or "").splitlines():
            if not _selector_covers_ref(selector, ref, default_branch=default_branch):
                self._run(["update-ref", "-d", ref], cwd=bare)

    def _ref_exists(self, bare: Path, ref: str) -> bool:
        result = self._run(
            ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=bare,
            capture_text=True,
            check=False,
        )
        return result.returncode == 0

    def _remove_managed_worktrees(self, bare: Path, *, managed_root: Path) -> None:
        result = cast(
            subprocess.CompletedProcess[str],
            self._run(
                ["worktree", "list", "--porcelain"],
                cwd=bare,
                capture_text=True,
                check=False,
            ),
        )
        if result.returncode != 0:
            return
        for line in (result.stdout or "").splitlines():
            if not line.startswith("worktree "):
                continue
            worktree = Path(line.removeprefix("worktree ")).expanduser().resolve()
            if worktree == bare or not worktree.is_relative_to(managed_root / "worktrees"):
                continue
            removed = self._run(
                ["worktree", "remove", "--force", str(worktree)],
                cwd=bare,
                check=False,
                timeout=self._slow_timeout,
            )
            if removed.returncode != 0 and worktree.exists():
                shutil.rmtree(worktree)
        self._run(["worktree", "prune"], cwd=bare, check=False)

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        capture_text: bool = False,
        capture_bytes: bool = False,
        check: bool = True,
        timeout: float | None = None,
        auth_header: str | None = None,
        auth_url: str | None = None,
    ) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        if self._git_path is None:
            raise GitCorpusError(f"`{self._git}` not found on PATH")
        effective_timeout = self._timeout if timeout is None else timeout
        env = None
        auth_config_path: Path | None = None
        if auth_header is not None:
            env, auth_config_path = _auth_config_env(auth_header, auth_url=auth_url)
        # Never inherit the CLI's stdio: stray git chatter would corrupt piped
        # output, and piping stderr keeps failure text available for errors.
        capture_stdout = subprocess.PIPE if capture_text or capture_bytes else subprocess.DEVNULL
        capture_stderr = subprocess.PIPE
        try:
            result = subprocess.run(
                [self._git_path, *args],
                cwd=cwd,
                env=env,
                text=capture_text,
                stdout=capture_stdout,
                stderr=capture_stderr,
                check=False,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitCorpusError(
                f"git {' '.join(args)} timed out after {effective_timeout:g}s"
            ) from exc
        finally:
            if auth_config_path is not None:
                auth_config_path.unlink(missing_ok=True)
        if check and result.returncode != 0:
            stderr = _stderr_text(result)
            raise GitCorpusError(
                f"git {' '.join(args)} failed: {_redact(stderr, auth_header) or 'no stderr'}"
            )
        return result


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
    safe_name = _safe_path_part(base_name[:-4])
    return cache_dir.expanduser() / _safe_path_part(host) / f"{safe_name}-{digest}.git"


def git_auth_header(token: str) -> str:
    """Return the transient Git HTTP auth header for a GitHub token."""
    credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"AUTHORIZATION: basic {credential}"


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


def _join_globs(stored: tuple[str, ...], requested: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*stored, *requested)))


def _profile_refspecs(profile: RefProfile, branch: str) -> tuple[str, ...]:
    if profile == "default":
        return (f"+refs/heads/{branch}:refs/heads/{branch}",)
    if profile == "branches":
        return ("+refs/heads/*:refs/heads/*",)
    if profile == "tags":
        return ("+refs/tags/*:refs/tags/*",)
    return ("+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")


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


def _short_ref(ref: str) -> str:
    if ref.startswith("refs/heads/"):
        return ref.removeprefix("refs/heads/")
    if ref.startswith("refs/tags/"):
        return ref.removeprefix("refs/tags/")
    return ref


def _order_refs(refs: tuple[str, ...], *, default_branch: str) -> tuple[str, ...]:
    ordered = sorted(ref for ref in refs if ref != default_branch)
    if default_branch in refs:
        return (default_branch, *ordered)
    return tuple(ordered)


def _cached_bare(repo: CorpusRepoTarget, *, root: Path) -> Path:
    bare = cache_path_for(_remote_url(repo), cache_dir=root)
    if not (bare / "HEAD").is_file():
        raise GitCorpusError("repository is not in the local corpus")
    return bare


def _blob_oid(cache: GitCorpusCache, bare: Path, *, ref: str, path: str) -> str:
    result = cast(
        subprocess.CompletedProcess[str],
        cache._run(["rev-parse", "--verify", f"{ref}:{path}"], cwd=bare, capture_text=True),
    )
    return (result.stdout or "").strip()


def _auth_header_for_url(url: str, auth_header: str | None) -> str | None:
    if auth_header is None:
        return None
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return auth_header
    if parsed.scheme == "file":
        return None
    if parsed.scheme or url.startswith("git@"):
        raise GitCorpusError("authenticated Git corpus sync requires an HTTPS clone_url")
    return None


def _parse_ref_grep_output(payload: bytes, *, ref: str) -> tuple[tuple[str, int, str], ...]:
    if not payload:
        return ()
    rows: list[tuple[str, int, str]] = []
    prefix = f"{ref}:"
    cursor = 0
    while cursor < len(payload):
        raw_ref_path, cursor = _read_until(payload, cursor, b"\0")
        raw_line, cursor = _read_until(payload, cursor, b"\0")
        _raw_column, cursor = _read_until(payload, cursor, b"\0")
        raw_text, cursor = _read_until(payload, cursor, b"\n")
        ref_path = raw_ref_path.decode(errors="replace")
        if not ref_path.startswith(prefix):
            raise GitCorpusError("could not parse git grep output: malformed ref/path")
        try:
            line = int(raw_line.decode())
        except ValueError as exc:
            raise GitCorpusError("could not parse git grep output: invalid line") from exc
        rows.append(
            (
                ref_path.removeprefix(prefix),
                line,
                raw_text.decode(errors="replace").rstrip("\n"),
            )
        )
    return tuple(rows)


def _read_until(payload: bytes, cursor: int, delimiter: bytes) -> tuple[bytes, int]:
    end = payload.find(delimiter, cursor)
    if end == -1:
        raise GitCorpusError("could not parse git grep output")
    return payload[cursor:end], end + len(delimiter)


def _worktree_path(repo: str, ref: str, *, root: Path) -> Path:
    digest = hashlib.sha256(f"{repo}@{ref}".encode()).hexdigest()[:12]
    name = f"{_safe_path_part(repo)}-{_safe_path_part(ref)}-{digest}"
    return root.expanduser() / "worktrees" / name


def _safe_path_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def _write_metadata(path: Path, data: dict[str, object]) -> None:
    target = path / METADATA_FILE
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True) + "\n")
    os.replace(tmp, target)


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


def _auth_config_env(auth_header: str, *, auth_url: str | None) -> tuple[dict[str, str], Path]:
    if auth_url is None:
        raise GitCorpusError("authenticated Git operation missing HTTPS remote URL")
    parsed = urlparse(auth_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise GitCorpusError("authenticated Git corpus sync requires an HTTPS clone_url")
    scope = f"https://{parsed.netloc}/"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="untaped-git-auth-",
        suffix=".config",
        delete=False,
    ) as auth_config:
        auth_config.write(f'[http "{scope}"]\n')
        auth_config.write(f"\textraheader = {auth_header}\n")
        path = Path(auth_config.name)
    env = os.environ.copy()
    # Git/curl trace output can include the injected Authorization header.
    for key in list(env):
        if key.startswith("GIT_TRACE") or key == "GIT_CURL_VERBOSE":
            env.pop(key, None)
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


def _stderr_text(
    result: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes],
) -> str:
    stderr = result.stderr
    if isinstance(stderr, bytes):
        return stderr.decode(errors="replace").strip()
    return (stderr or "").strip()


def _redact(value: str, secret: str | None) -> str:
    if secret is None:
        return value
    return value.replace(secret, "<redacted>")
