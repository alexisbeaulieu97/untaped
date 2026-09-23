"""GitHub-backed dependency read adapter for live dependency graphing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from untaped.api import HttpError, UntapedError, bounded_map
from untaped.capabilities.ansible.domain.identity import IdentityResolver, repo_key
from untaped.capabilities.ansible.domain.parser import parse_dependency_file
from untaped.capabilities.ansible.domain.payloads import (
    CachedRef,
    IndexedDependency,
    SkippedDependencyFile,
)
from untaped.capabilities.ansible.errors import AnsibleError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from untaped.capabilities.ansible.application.ports import (
        DependencyIndex,
        GitHubDependencyReader,
    )


@dataclass
class _LiveRead:
    """Outcome of one live repo/ref read: edges plus what to tell the user."""

    edges: list[IndexedDependency] = field(default_factory=list)
    skipped: list[SkippedDependencyFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class GithubDependencyIndex:
    """Read declared dependencies live from GitHub, with indexed impact fallback.

    A failed read (deleted repo, missing tag, permission error) is recorded in
    :attr:`errors` and leaves that node unexpanded instead of aborting the
    whole graph. Uncached pairs of a batch are read concurrently.
    """

    def __init__(
        self,
        *,
        github: GitHubDependencyReader,
        wrapped: DependencyIndex,
        aliases: dict[str, str],
        dependency_paths: list[str],
        github_host: str | None = None,
        concurrency: int = 1,
    ) -> None:
        self._github = github
        self._github_host = github_host
        self._concurrency = concurrency
        self._wrapped = wrapped
        self._aliases = aliases
        self._dependency_paths = dependency_paths
        self._cache: dict[tuple[str, str | None], list[IndexedDependency]] = {}
        self._warnings: list[SkippedDependencyFile] = []
        self._errors: list[str] = []

    @property
    def warnings(self) -> tuple[SkippedDependencyFile, ...]:
        """Parse warnings accumulated during live dependency reads."""
        return tuple(self._warnings)

    @property
    def errors(self) -> tuple[str, ...]:
        """Per-repo read failures and incomplete listings, as graph warnings."""
        return tuple(self._errors)

    def dependencies(
        self,
        repo: str,
        ref: str | None,
        *,
        source_key: str | None,
    ) -> list[IndexedDependency]:
        return self.dependencies_batch([(repo, ref)], source_key=source_key)[(repo, ref)]

    def dependents(
        self,
        repo: str,
        ref: str | None,
        *,
        source_key: str | None,
    ) -> list[IndexedDependency]:
        return self._wrapped.dependents(repo, ref, source_key=source_key)

    def dependencies_batch(
        self,
        pairs: Sequence[tuple[str, str | None]],
        *,
        source_key: str | None,
    ) -> dict[tuple[str, str | None], list[IndexedDependency]]:
        requested = list(dict.fromkeys(pairs))
        pending = list(
            {
                (repo_key(repo), ref): (repo, ref)
                for repo, ref in requested
                if (repo_key(repo), ref) not in self._cache
            }.values()
        )
        reads: dict[tuple[str, str | None], _LiveRead] = {}

        def record(pair: tuple[str, str | None], read: _LiveRead) -> None:
            reads[pair] = read

        if pending:
            bounded_map(
                lambda pair: self._live_read(*pair),
                pending,
                concurrency=self._concurrency,
                on_each=record,
            )
        # Record in request order so warnings are deterministic under concurrency.
        for repo, ref in pending:
            read = reads[(repo, ref)]
            self._cache[(repo_key(repo), ref)] = read.edges
            self._warnings.extend(read.skipped)
            self._errors.extend(read.errors)
        return {(repo, ref): self._cache[(repo_key(repo), ref)] for repo, ref in requested}

    def dependents_batch(
        self,
        pairs: Sequence[tuple[str, str | None]],
        *,
        source_key: str | None,
    ) -> dict[tuple[str, str | None], list[IndexedDependency]]:
        return self._wrapped.dependents_batch(pairs, source_key=source_key)

    def cached_refs(self, repo: str, *, source_key: str | None) -> set[str]:
        refs = set(self._wrapped.cached_refs(repo, source_key=source_key))
        refs.update(
            cached_ref
            for cached_repo, cached_ref in self._cache
            if cached_repo == repo_key(repo) and cached_ref is not None
        )
        return refs

    def cached_ref_metadata(self, repo: str, *, source_key: str | None) -> tuple[CachedRef, ...]:
        return self._with_live_refs(
            repo,
            self._wrapped.cached_ref_metadata(repo, source_key=source_key),
        )

    def cached_ref_metadata_batch(
        self,
        repos: Sequence[str],
        *,
        source_key: str | None,
    ) -> dict[str, tuple[CachedRef, ...]]:
        wrapped = self._wrapped.cached_ref_metadata_batch(repos, source_key=source_key)
        return {repo: self._with_live_refs(repo, metadata) for repo, metadata in wrapped.items()}

    def is_stale(self, source_key: str | None, *, max_age_seconds: int) -> bool:
        return self._wrapped.is_stale(source_key, max_age_seconds=max_age_seconds)

    def _with_live_refs(
        self,
        repo: str,
        wrapped: tuple[CachedRef, ...],
    ) -> tuple[CachedRef, ...]:
        metadata = list(wrapped)
        known = {(cached_ref.name, cached_ref.kind) for cached_ref in metadata}
        for cached_repo, cached_ref in self._cache:
            if cached_repo != repo_key(repo) or cached_ref is None or (cached_ref, None) in known:
                continue
            metadata.append(CachedRef(name=cached_ref))
        return tuple(metadata)

    def _live_read(self, repo: str, ref: str | None) -> _LiveRead:
        read = _LiveRead()
        label = f"{repo}@{ref}" if ref else repo
        try:
            self._read_into(read, repo, ref)
        except UntapedError as exc:
            if isinstance(exc, HttpError) and exc.status_code in _GLOBAL_FAILURE_STATUSES:
                raise
            read.edges.clear()
            read.errors.append(
                f"could not read {label} live: {exc}; not expanding its dependencies"
            )
        return read

    def _read_into(self, read: _LiveRead, repo: str, ref: str | None) -> None:
        owner, name = _split_repo(repo)
        read_ref = self._read_ref(owner, name, ref)
        source_ref = ref or read_ref
        paths, truncated = self._tree_paths(owner, name, read_ref)
        if truncated:
            read.errors.append(
                f"GitHub truncated the file listing for {repo}@{source_ref}; "
                "dependency files missing from it were not read"
            )
        resolver = IdentityResolver(self._aliases, github_host=self._github_host)
        edges = read.edges
        for path in self._dependency_paths:
            if path not in paths:
                continue
            report = parse_dependency_file(
                path,
                self._github.get_raw_content(owner, name, path, ref=read_ref),
            )
            read.skipped.extend(
                SkippedDependencyFile(
                    repo=repo,
                    ref=source_ref,
                    source_path=warning.source_path,
                    reason=warning.reason,
                )
                for warning in report.warnings
            )
            for declaration in report.dependencies:
                resolved = resolver.resolve(declaration)
                edges.append(
                    IndexedDependency(
                        source_repo=repo,
                        source_ref=source_ref,
                        dependency_repo=resolved.repo,
                        dependency_name=declaration.name,
                        dependency_version=declaration.version,
                        source_path=path,
                        unresolved=resolved.unresolved,
                    )
                )

    def _read_ref(self, owner: str, repo: str, ref: str | None) -> str:
        if ref is None:
            return _default_branch(self._github.get_repository(owner, repo))
        return self._resolved_ref_sha(owner, repo, ref) or ref

    def _resolved_ref_sha(self, owner: str, repo: str, ref: str) -> str | None:
        for namespace in ("heads", "tags"):
            expected_ref = f"refs/{namespace}/{ref}"
            for row in self._github.list_matching_refs(owner, repo, f"{namespace}/{ref}"):
                if not isinstance(row, dict) or row.get("ref") != expected_ref:
                    continue
                ref_object = row.get("object")
                if not isinstance(ref_object, dict):
                    continue
                sha = ref_object.get("sha")
                if isinstance(sha, str) and sha:
                    return sha
        return None

    def _tree_paths(self, owner: str, repo: str, ref: str) -> tuple[set[str], bool]:
        response = self._github.get_tree(owner, repo, ref, recursive=True)
        truncated = response.get("truncated") is True
        tree = response.get("tree")
        if not isinstance(tree, list):
            return set(), truncated
        paths = set()
        for entry in tree:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if isinstance(path, str) and path:
                paths.add(path)
        return paths, truncated


# Auth and rate-limit failures hit every repo alike: surface them as one error.
_GLOBAL_FAILURE_STATUSES = frozenset({401, 429})


def _split_repo(repo: str) -> tuple[str, str]:
    owner, separator, name = repo.partition("/")
    if not owner or not separator or not name:
        raise AnsibleError(f"repo must be owner/name (got {repo!r})")
    return owner, name


def _default_branch(repository: dict[str, object]) -> str:
    default_branch = repository.get("default_branch")
    if isinstance(default_branch, str) and default_branch:
        return default_branch
    return "HEAD"
