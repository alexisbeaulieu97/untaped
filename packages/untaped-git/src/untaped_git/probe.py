"""Ref probes: each URL's branches and tags, from its host's batch probe or from ``ls-remote``.

A forge plugin may fill :meth:`GitHost.probe_refs <untaped_git.hosts.GitHost.probe_refs>`
to read many repositories' refs in a few requests. :func:`probe_refs` asks
the provider chosen for each URL's host (:func:`~untaped_git.hosts.host_plugin`,
which asks no one for credentials) for all of that host's URLs at once. It
reads with ``git ls-remote``, one URL at a time and ``parallel`` at once,
every URL whose host has no probe, whose host's probe failed as
``unavailable``, or that the probe could not read for any reason but
``not_found``. A probe's other failure is raised. Each host's budget after
its probe is kept, so a caller can stop before spending the rest.

Refs are full names (``refs/heads/main``), and an annotated tag maps to the
commit it points at.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import (
    ErrorCategory,
    Failure,
    Record,
    UntapedError,
    bounded_map,
    experimental,
)
from untaped.sdk.contracts import Errored, NoProviderReady, Ok, gather

#: Ref kinds a probe reads: branches (``heads``) and tags.
REF_KINDS = ("heads", "tags")


@experimental
class ProbedRepo(Record):
    """One URL's refs as a probe read them, or why it could not."""

    url: str = Field(description="The URL probed.")
    refs: dict[str, str] = Field(
        default_factory=dict,
        description="Full ref name (`refs/heads/main`) to the commit it points at.",
    )
    default_branch: str | None = Field(
        default=None, description="The branch the repository's HEAD points at, when known."
    )
    failure: Failure | None = Field(
        default=None, description="Why this URL was not read; its other fields are then empty."
    )


@experimental
class RefProbe(Record):
    """What a host's batch probe read, and what is left of its budget."""

    repos: list[ProbedRepo] = Field(default_factory=list, description="One row per URL asked.")
    remaining: int | None = Field(
        default=None, description="The host's request budget after this probe, when it has one."
    )
    reset_at: datetime | None = Field(default=None, description="When that budget resets.")
    exhausted: bool = Field(
        default=False,
        description="The budget is below the host's own floor: ask it nothing more before reset.",
    )


class HostBudget(BaseModel):
    """A host's probe budget after :func:`probe_refs` asked it."""

    model_config = ConfigDict(frozen=True)

    plugin: str
    host: str
    remaining: int | None = None
    reset_at: datetime | None = None
    exhausted: bool = False


class ProbeResult(BaseModel):
    """Every URL :func:`probe_refs` was asked about, and how it was read.

    ``fallbacks`` maps each URL read with ``ls-remote`` although its host
    has a probe to why; ``budgets`` holds each probing host's budget.
    """

    model_config = ConfigDict(frozen=True)

    repos: dict[str, ProbedRepo] = Field(default_factory=dict)
    fallbacks: dict[str, str] = Field(default_factory=dict)
    budgets: tuple[HostBudget, ...] = ()


def probe_refs(
    urls: Sequence[str],
    *,
    kinds: Sequence[str] = REF_KINDS,
    default_branch_only: bool = False,
    default_branches: Mapping[str, str] | None = None,
    parallel: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ProbeResult:
    """Each of ``urls``' refs of ``kinds`` (or only its default branch), by its host's probe.

    ``default_branches`` holds a URL's default branch where the caller knows
    it, so ``ls-remote`` needn't ask. ``parallel`` defaults to
    ``git.probe_parallel``. ``on_progress(done, total)`` counts URLs.
    """
    from untaped_git.hosts import host_plugin  # noqa: PLC0415 - a cycle: hosts imports this
    from untaped_git.settings import git_settings  # noqa: PLC0415 - the owner's own section
    from untaped_git.url import url_host  # noqa: PLC0415 - kept beside its uses

    parallel = parallel or git_settings().probe_parallel

    known = dict(default_branches or {})
    asking = list(dict.fromkeys(urls))
    by_plugin: dict[str, list[str]] = defaultdict(list)
    plain: list[str] = []
    plugins: dict[str, str | None] = {}  # host → its prober, chosen once per host
    for url in asking:
        host = url_host(url)
        if host is not None and host not in plugins:
            plugins[host] = host_plugin(host)
        plugin = plugins.get(host) if host is not None else None
        (by_plugin[plugin] if plugin is not None else plain).append(url)
    progress = _Progress(len(asking), on_progress)
    repos: dict[str, ProbedRepo] = {}
    fallbacks: dict[str, str] = {}
    budgets: list[HostBudget] = []
    for plugin, asked in by_plugin.items():
        probe = _ask(
            plugin, asked, kinds=kinds, default_branch_only=default_branch_only, parallel=parallel
        )
        if isinstance(probe, str):
            fallbacks.update(dict.fromkeys(asked, probe))
            plain.extend(asked)
            continue
        if probe is None:  # the host has no probe
            plain.extend(asked)
            continue
        budgets.append(_budget(plugin, asked[0], probe))
        read = {row.url: row for row in probe.repos}
        for url in asked:
            row = read.get(url)
            if row is None or (row.failure and row.failure.category != ErrorCategory.NOT_FOUND):
                why = row.failure.message if row and row.failure else "it did not answer for it"
                fallbacks[url] = f"{plugin} could not probe it ({why})"
                plain.append(url)
                continue
            repos[url] = row
            progress.step()
    _ls_remote_all(
        [url for url in plain if url not in repos],
        repos,
        kinds=kinds,
        default_branch_only=default_branch_only,
        known=known,
        parallel=parallel,
        progress=progress,
    )
    return ProbeResult(repos=repos, fallbacks=fallbacks, budgets=tuple(budgets))


def _ask(
    plugin: str,
    urls: list[str],
    *,
    kinds: Sequence[str],
    default_branch_only: bool,
    parallel: int,
) -> RefProbe | str | None:
    """``plugin``'s probe of ``urls``; ``None`` when it has none, why it failed when unavailable."""
    from untaped_git.hosts import GitHost  # noqa: PLC0415 - a cycle: hosts imports this

    try:
        answers = gather(GitHost.probe_refs, plugins=[plugin])(
            list(urls),
            kinds=list(kinds),
            default_branch_only=default_branch_only,
            parallel=parallel,
        )
    except NoProviderReady:
        return None
    for answer in answers:
        if isinstance(answer, Ok):
            return answer.value
        if isinstance(answer, Errored):
            error = answer.error
            if error.category == ErrorCategory.UNAVAILABLE:
                return f"{plugin}'s probe failed: {error}"
            raise error
    return None  # not filled, or set aside: plain git


def _budget(plugin: str, url: str, probe: RefProbe) -> HostBudget:
    from untaped_git.url import url_host  # noqa: PLC0415 - kept beside its one use

    return HostBudget(
        plugin=plugin,
        host=url_host(url) or "",
        remaining=probe.remaining,
        reset_at=probe.reset_at,
        exhausted=probe.exhausted,
    )


def _ls_remote_all(
    urls: list[str],
    repos: dict[str, ProbedRepo],
    *,
    kinds: Sequence[str],
    default_branch_only: bool,
    known: Mapping[str, str],
    parallel: int,
    progress: _Progress,
) -> None:
    def read(url: str) -> ProbedRepo:
        return ls_remote_probe(
            url, kinds=kinds, default_branch_only=default_branch_only, default=known.get(url)
        )

    def record(url: str, row: ProbedRepo) -> None:
        repos[url] = row
        progress.step()

    bounded_map(read, urls, concurrency=max(1, parallel), on_each=record)


def ls_remote_probe(
    url: str,
    *,
    kinds: Sequence[str] = REF_KINDS,
    default_branch_only: bool = False,
    default: str | None = None,
) -> ProbedRepo:
    """``url``'s refs read with ``git ls-remote``; a failure is the row's ``failure``."""
    from untaped_git.api import default_branch, ls_remote  # noqa: PLC0415 - api imports this

    try:
        branch = default or default_branch(url)
        if default_branch_only:
            patterns = ["HEAD", *([f"refs/heads/{branch}"] if branch else [])]
        else:
            patterns = ["HEAD", *(f"refs/{kind}/*" for kind in REF_KINDS if kind in kinds)]
        found = ls_remote(url, patterns)
    except UntapedError as exc:
        return ProbedRepo(url=url, failure=Failure.from_exception(exc))
    if default_branch_only:
        sha = found.get(f"refs/heads/{branch}") if branch else None
        sha = sha or found.get("HEAD")
        refs = {f"refs/heads/{branch}": sha} if branch and sha else {}
    else:
        refs = {ref: sha for ref, sha in found.items() if ref != "HEAD"}
    return ProbedRepo(url=url, refs=refs, default_branch=branch)


class _Progress:
    def __init__(self, total: int, report: Callable[[int, int], None] | None) -> None:
        self._total = total
        self._done = 0
        self._report = report

    def step(self) -> None:
        self._done += 1
        if self._report is not None:
            self._report(self._done, self._total)


__all__ = ["REF_KINDS", "HostBudget", "ProbeResult", "ProbedRepo", "RefProbe", "probe_refs"]
