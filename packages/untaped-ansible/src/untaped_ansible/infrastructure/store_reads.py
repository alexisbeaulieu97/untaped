"""Dependencies read live from each repo's provider, through the repo store.

With no source to answer, a graph reads a repo where its provider lists it:
the name is located over the plugins that list repos (``RepoFinder``), the
ref is listed with ``ls-remote``, fetched into ansible's namespace of the
repo store only when it moved (so a second read fetches nothing), and the
dependency files are read from it as a source refresh reads them.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from untaped.sdk import ErrorCategory, UntapedError, bounded_map
from untaped_ansible.domain.identity import IdentityResolver, repo_key
from untaped_ansible.domain.parser import parse_dependency_file
from untaped_ansible.domain.payloads import (
    CachedRef,
    GitRef,
    IndexedDependency,
    SkippedDependencyFile,
)
from untaped_ansible.errors import AnsibleError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from untaped_ansible.application.ports import DependencyIndex, GitCache
    from untaped_git.api import Repo

#: A listing's failures that hit every repo alike: a rejected credential, missing setup.
_GLOBAL = frozenset({ErrorCategory.AUTH, ErrorCategory.CONFIG})


@dataclass
class _LiveRead:
    """Outcome of one live repo/ref read: edges plus what to tell the user."""

    edges: list[IndexedDependency] = field(default_factory=list)
    skipped: list[SkippedDependencyFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    failure: UntapedError | None = None
    default_branch: str | None = None


class StoreDependencyIndex:
    """Read declared dependencies live through the repo store, with indexed impact fallback.

    ``locate`` names a repo's provider listing (its URL and default
    branch), ``remote_refs`` lists a URL's refs (``ls-remote``) and
    ``default_branch`` asks a URL's ``HEAD``, which wins over the listing's
    possibly cached default. A failed read (an unknown name, a deleted repo
    or tag, a clone the credential can't reach) is recorded in
    :attr:`errors` and :meth:`failure` and leaves that node unexpanded
    instead of aborting the whole graph; a listing's rejected credential or
    missing setup fails every repo alike, so it raises, as does git's own
    missing setup. Uncached pairs of a batch are read concurrently.
    """

    def __init__(
        self,
        *,
        locate: Callable[[str], Repo],
        remote_refs: Callable[[str, Sequence[str]], dict[str, str]],
        default_branch: Callable[[str], str | None],
        store: GitCache,
        wrapped: DependencyIndex,
        resolver: IdentityResolver,
        dependency_paths: list[str],
        concurrency: int = 1,
    ) -> None:
        self._locate = locate
        self._located: dict[str, Repo | UntapedError] = {}
        self._locating = threading.Lock()
        self._remote_refs = remote_refs
        self._default_branch = default_branch
        self._store = store
        self._concurrency = concurrency
        self._wrapped = wrapped
        self._resolver = resolver
        self._dependency_paths = dependency_paths
        self._cache: dict[tuple[str, str | None], list[IndexedDependency]] = {}
        self._failures: dict[tuple[str, str | None], UntapedError] = {}
        # Default branch resolved by each ref-less read, keyed by repo_key.
        self._default_branches: dict[str, str] = {}
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

    def failure(self, repo: str, ref: str | None) -> UntapedError | None:
        """Why ``repo`` at ``ref`` could not be read live, if it was read and failed."""
        return self._failures.get((repo_key(repo), ref))

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
        for repo, ref in requested:
            # A ref-less read already read the default branch: reuse it.
            key = repo_key(repo)
            if ref is not None and self._default_branches.get(key) == ref:
                self._cache.setdefault((key, ref), self._cache[(key, None)])
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
            if read.failure is not None:
                self._failures[(repo_key(repo), ref)] = read.failure
            if read.default_branch is not None:
                self._default_branches[repo_key(repo)] = read.default_branch
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
        default = self._default_branches.get(repo_key(repo))
        if default is None and (repo_key(repo), None) not in self._cache:
            return tuple(metadata)
        if default is not None and all(cached_ref.name != default for cached_ref in metadata):
            metadata.append(CachedRef(name=default))
        # Once read live, the live default (even none) overrides a possibly renamed cached one.
        return tuple(
            cached_ref
            if cached_ref.default_branch == default
            else cached_ref.model_copy(update={"default_branch": default})
            for cached_ref in metadata
        )

    def _live_read(self, repo: str, ref: str | None) -> _LiveRead:
        read = _LiveRead()
        try:
            located = self._located_repo(repo)
        except UntapedError as exc:
            if exc.category in _GLOBAL:
                raise
            return _failed(read, repo, ref, exc)
        try:
            self._read_into(read, repo, ref, located.url, located.default_branch)
        except UntapedError as exc:
            # One clone's rejected credential is that repo's; git's missing setup is everyone's.
            if exc.category is ErrorCategory.CONFIG:
                raise
            return _failed(read, repo, ref, exc)
        return read

    def _read_into(
        self, read: _LiveRead, repo: str, ref: str | None, url: str, listed_default: str | None
    ) -> None:
        source_ref = ref or self._default_branch(url) or listed_default
        if source_ref is None:
            raise AnsibleError(f"{repo} has no default branch", category="not_found")
        git_ref = self._ref(url, source_ref)
        self._store.fetch(url, [git_ref])
        files = self._store.read_files(url, [git_ref.sha], self._dependency_paths)[git_ref.sha]
        if ref is None:
            read.default_branch = source_ref
        resolver = self._resolver
        edges = read.edges
        for path in self._dependency_paths:
            if path not in files:
                continue
            report = parse_dependency_file(path, files[path])
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

    def _located_repo(self, repo: str) -> Repo:
        """``repo`` as its provider lists it, located once per name however many reads ask.

        Locating holds the lock: the finder asks its providers once, on first
        use, and is not shared safely between threads.
        """
        with self._locating:
            key = repo_key(repo)
            if key not in self._located:
                try:
                    self._located[key] = self._locate(repo)
                except UntapedError as exc:
                    self._located[key] = exc
            located = self._located[key]
        if isinstance(located, UntapedError):
            raise located
        return located

    def _ref(self, url: str, name: str) -> GitRef:
        """The branch ``name`` of ``url``, else its tag: the store fetches names, not commits."""
        listed = self._remote_refs(url, [f"refs/heads/{name}", f"refs/tags/{name}"])
        for kind in ("heads", "tags"):
            sha = listed.get(f"refs/{kind}/{name}")
            if sha:
                return GitRef(kind=kind, name=name, sha=sha)
        raise AnsibleError(f"no branch or tag named {name!r}", category="not_found")


def _failed(read: _LiveRead, repo: str, ref: str | None, exc: UntapedError) -> _LiveRead:
    """``read`` emptied, with ``exc`` (naming the repo) as its failure and its warning."""
    label = f"{repo}@{ref}" if ref else repo
    message = f"could not read {label} live: {exc}"
    hint = f" ({exc.hint})" if exc.hint else ""
    read.edges.clear()
    read.skipped.clear()
    read.default_branch = None
    read.failure = AnsibleError(message, category=exc.category, system=exc.system, hint=exc.hint)
    read.failure.__cause__ = exc
    read.errors.append(f"{message}{hint}; not expanding its dependencies")
    return read
