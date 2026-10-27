"""``untaped ansible source``: saved GitHub sources, their cached data and its refresh."""

from __future__ import annotations

import shlex
from collections.abc import Iterator

from pydantic import Field

from untaped.sdk import (
    Confirm,
    Failure,
    FreshnessRecord,
    Group,
    Output,
    Parallel,
    Positional,
    UntapedError,
    key,
    most_severe,
    not_found,
    now,
    plural,
    q,
    run_hint,
    size_text,
)
from untaped_ansible.application.refresh_git_index import RefreshResult
from untaped_ansible.commands._ansible import (
    BACKEND,
    REF_SCAN_DEFAULT,
    TEAM,
    AnsibleCommand,
    RefScanDefault,
    saved_source_key,
    source_definition,
)
from untaped_ansible.commands._changes import Changes, edited
from untaped_ansible.domain.identity import repo_key
from untaped_ansible.errors import AnsibleError
from untaped_ansible.infrastructure import (
    GitSourceStore,
    SourceRepository,
    SqliteDependencyIndex,
    open_index,
)
from untaped_ansible.infrastructure.github_ref_probe import is_transient_ref_probe_failure
from untaped_ansible.records import Source, SourceOutcome, SourceStatus
from untaped_ansible.refresh import (
    GIT_PARALLEL_CAP,
    Backend,
    RefreshRecord,
    SelectedSource,
    fill_summary,
    repo_errors,
    serve_sources,
    sync_freshness,
    warn_refresh,
)
from untaped_ansible.settings import SOURCE_DATA, SOURCES, AnsibleSettings, SourceDefinition
from untaped_git.api import Removed, store_key

NAME = "Source name."


class Set(AnsibleCommand):
    """Create or replace a reusable GitHub source.

    It replaces the whole definition; `source patch` adds or removes one
    org, team, repo, path or ref selector. A change drops the source's cached
    data, so the next `deps`, `impact`, `find` or `graph` that reads it
    refreshes it first.

    Examples
    --------
    ```console
    $ untaped ansible source set platform --org acme --team acme/platform
    ```
    """

    name: Positional[str] = Field(description=NAME)
    org: list[str] = Field(default_factory=list, description="GitHub org to scan.")
    team: list[str] = Field(default_factory=list, description=TEAM)
    repo: list[str] = Field(default_factory=list, description="GitHub repo as owner/name.")
    path: list[str] = Field(default_factory=list, description="Dependency file path.")
    ref_kind: list[str] = Field(
        default_factory=list,
        description="Ref namespace to scan: heads or tags; omit to use ansible.ref_scan_default.",
    )
    ref_pattern: list[str] = Field(
        default_factory=list,
        description="fnmatch pattern for branch/tag names; omit to use ansible.ref_scan_default.",
    )
    ref_scan_default: RefScanDefault | None = Field(None, description=REF_SCAN_DEFAULT)
    output: Output[SourceOutcome]

    def run(self) -> SourceOutcome:
        source = source_definition(
            name=self.name,
            orgs=self.org,
            teams=self.team,
            repos=self.repo,
            paths=self.path,
            ref_kinds=self.ref_kind,
            ref_patterns=self.ref_pattern,
            ref_scan_default=self.ref_scan_default,
        )
        sources = SourceRepository()
        previous = sources.get(self.name)
        action = "created" if previous is None else "unchanged" if previous == source else "updated"
        if action != "unchanged":
            sources.upsert(source)
            open_index().clear(saved_source_key(self.name))
        return SourceOutcome(action=action, name=self.name)


class Patch(AnsibleCommand):
    """Patch a saved GitHub source definition.

    A change drops the source's cached data, as `source set` does;
    `changes` on the outcome lists what was added or removed.

    Examples
    --------
    ```console
    $ untaped ansible source patch platform --add-repo acme/site --remove-org legacy
    ```
    """

    name: Positional[str] = Field(description=NAME)
    edit: Changes
    output: Output[SourceOutcome]

    def run(self) -> SourceOutcome:
        previous = self.saved(self.name)
        source, changes = edited(previous, self.edit)
        if previous == source:
            return SourceOutcome(action="unchanged", name=self.name)
        SourceRepository().upsert(source)
        open_index().clear(saved_source_key(self.name))
        return SourceOutcome(action="updated", name=self.name, changes=changes)


class List(AnsibleCommand):
    """List saved sources."""

    output: Output[Source]

    def run(self) -> Iterator[Source]:
        self.output.empty("No sources configured. Add one with `untaped ansible source set NAME`.")
        for source in SourceRepository().entries():
            yield _source_row(source)


class Get(AnsibleCommand):
    """Show one saved source."""

    name: Positional[str] = Field(description=NAME)
    output: Output[Source]

    def run(self) -> Source:
        return _source_row(self.saved(self.name))


class Remove(AnsibleCommand):
    """Remove a saved source, its cached source data and the repos only it used.

    A repo another saved source still selects is kept. Every other repo the
    source selected is released from the repo store first: `removed` when no
    other plugin holds it, else `released` with who kept it; a repo kept for
    another saved source reads `kept`. With --dry-run each reads
    `would release`. The source and its index rows go only once every
    release succeeded; otherwise `action` is `failed` and a rerun retries.
    Confirms first; without a terminal it needs --yes.
    """

    removes = SOURCES

    name: Positional[str] = Field(description=NAME)
    confirm: Confirm
    output: Output[SourceOutcome]

    def run(self) -> SourceOutcome:
        self.saved(self.name)
        dry_run = not self.confirm(f"Remove source {q(self.name)} and its cached source data?")
        sources = SourceRepository()
        index = open_index()
        key = saved_source_key(self.name)
        others = {
            saved_source_key(source.name): source.name
            for source in sources.entries()
            if source.name != self.name
        }
        selected = index.selecting_sources(others)
        changes, failures = _release_repos(
            index.source_repos(key),
            {repo: sorted(others[source] for source in keys) for repo, keys in selected.items()},
            store=GitSourceStore(plugin=self.ctx.plugin),
            host=self.github_host,
            dry_run=dry_run,
        )
        if not dry_run and not failures:
            sources.remove(self.name)
            index.clear(key)
            SOURCE_DATA.forget(key)
        for repo, failure in failures:
            self.ctx.diag.failure(failure, item=repo)
        if failures:
            self.ctx.diag.hint(run_hint(f"ansible source remove {shlex.quote(self.name)}"))
        action = "planned" if dry_run else "failed" if failures else "deleted"
        error = (
            Failure.from_exception(
                most_severe(failure for _, failure in failures),
                message=f"{plural(len(failures), 'repo')} not released; source kept",
            )
            if failures
            else None
        )
        return SourceOutcome(action=action, name=self.name, changes=changes, error=error)


class Status(AnsibleCommand):
    """Show cached source data status.

    Without NAME, one row per saved source.
    """

    name: Positional[str | None] = Field(None, description="Source to inspect.")
    output: Output[SourceStatus]

    def run(self) -> Iterator[SourceStatus]:
        index = open_index()
        configured = {source.name: source for source in SourceRepository().entries()}
        if self.name is not None and self.name not in configured:
            raise AnsibleError(
                not_found("source", self.name, known=sorted(configured)), category="not_found"
            )
        self.output.empty("No sources scanned yet. Run `untaped ansible source refresh NAME`.")
        for name in [self.name] if self.name is not None else sorted(configured):
            yield _status_row(name, index=index, configured=name in configured)


class Refresh(AnsibleCommand):
    """Refresh a saved source from GitHub.

    --parallel defaults to ansible.git_fetch_parallel and is capped at 32.
    Progress goes to stderr. A failure for one repo does not stop the
    refresh: successes are saved, each failure prints `error: REPO: reason`,
    and the run exits 1, or 5 when any failure was transient, with
    `refresh completed with N repo failures; successes were saved`. Rerun
    it to retry only the failures. When every repo fails, the previous data
    is kept (`index left unchanged`).

    When GitHub's GraphQL budget drops below
    `ansible.source_refresh_rate_limit_floor`, the refresh stops with the
    finished batches saved and exits 5; rerunning resumes where it left off.
    Secondary rate limits, a rejected token, forbidden requests and unknown
    GraphQL access failures abort the whole refresh instead. The backends are
    described with the source data options (`untaped ansible deps --help`).

    Examples
    --------
    ```console
    $ untaped ansible source refresh platform --backend git
    ```
    """

    name: Positional[str] = Field(description=NAME)
    parallel: Parallel = Parallel(
        default=key(AnsibleSettings).git_fetch_parallel, cap=GIT_PARALLEL_CAP
    )
    backend: Backend | None = Field(None, description=BACKEND)

    def run(self) -> None:
        name = self.name
        source = self.saved(name)
        key = saved_source_key(name)
        wiring = self.wiring(open_index(), parallel=self.parallel.value, backend=self.backend)
        selection = SelectedSource(definition=source, key=key, label=name, subject=f"source {name}")
        record = RefreshRecord()
        try:
            served = serve_sources(
                [selection],
                refresh=True,
                read=lambda: None,
                wiring=wiring,
                # any failure raises before serve()'s stale line: this command serves no data
                empty=lambda _: True,
                record=record,
                warnings=False,
            )
        except UntapedError as exc:
            result, error = record.results.get(key), record.errors.get(key)
            if result is not None and error is None:
                # partial: serve() raised its failure over the saved successes
                self._report(result)
                raise _partial_refresh_error(result) from None
            if result is not None:  # paused, or every repo failed
                self._report_repo_failures(result)
                warn_refresh(result, self.settings, self.ctx.diag.warning)
            if error is not None:
                raise error from None  # the fill's own, without serve()'s hint to --cached
            if exc.details.get("key") == key:
                # serve() raised what the run this one waited on left (partial, failed)
                self.ctx.diag.info(self._refreshed_meanwhile())
                exc.hint = f"rerun `untaped ansible source refresh {shlex.quote(name)}` to retry"
            raise  # else no fill ran: the lock never came
        if key not in served.filled:
            # the lock's last holder refreshed it while this run waited: serve() kept that
            self.ctx.diag.info(self._refreshed_meanwhile())
            return
        self._report(record.results[key])

    def _refreshed_meanwhile(self) -> str:
        return f"source {self.name} was refreshed by another run while this one waited"

    def _report(self, result: RefreshResult) -> None:
        """The summary, then each repo that failed, then the refresh's warning groups."""
        self.ctx.diag.info(f"refreshed source {self.name}: {fill_summary(result)}")
        self._report_repo_failures(result)
        warn_refresh(result, self.settings, self.ctx.diag.warning)

    def _report_repo_failures(self, result: RefreshResult) -> None:
        """One failure line per repo the refresh could not read, and a rerun hint when it helps."""
        for failure, error in zip(result.failures, repo_errors(result), strict=True):
            self.ctx.diag.failure(error, item=failure.repo)
        if any(is_transient_ref_probe_failure(failure.reason) for failure in result.failures):
            self.ctx.diag.hint(
                f"rerun `untaped ansible source refresh {shlex.quote(self.name)}`; "
                "unchanged repos skip Git fetch and dependency scan work"
            )


def _partial_refresh_error(result: RefreshResult) -> AnsibleError:
    worst = most_severe(repo_errors(result))
    message = (
        f"refresh completed with {plural(len(result.failures), 'repo failure')}; "
        "successes were saved"
    )
    return AnsibleError(message, category=worst.category)


def _source_row(source: SourceDefinition) -> Source:
    return Source.model_validate(source.model_dump())


def _release_repos(
    repos: dict[str, tuple[str, ...]],
    selected: dict[str, list[str]],
    *,
    store: GitSourceStore,
    host: str | None,
    dry_run: bool,
) -> tuple[list[str], list[tuple[str, UntapedError]]]:
    """Release ``repos`` (``OWNER/NAME`` → clone URLs) no other saved source selects.

    Every repo is also tried at its ``https://<host>/OWNER/NAME.git`` URL, so
    one the source still selects but whose refs were all pruned is found
    too; https and ssh URLs of a repo are one store repo. Repos ansible has no
    part in are skipped. Returns the outcome lines and the failed releases.
    """
    changes: list[str] = []
    failures: list[tuple[str, UntapedError]] = []
    for repo, urls in repos.items():
        keepers = selected.get(repo_key(repo))
        if keepers:
            noun = "source" if len(keepers) == 1 else "sources"
            changes.append(f"kept {repo} (selected by {noun} {_and_list(keepers)})")
            continue
        candidates = [*([f"https://{host}/{repo}.git"] if host else []), *urls]
        for url in {store_key(url): url for url in candidates}.values():
            if dry_run:
                if store.holds(url):
                    changes.append(f"would release {repo}")
                continue
            try:
                outcome = store.release(url)
            except UntapedError as exc:
                failures.append((repo, exc))
                changes.append(f"failed to release {repo}")
                continue
            if isinstance(outcome, Removed):
                changes.append(f"removed {repo} ({size_text(outcome.freed_bytes)} freed)")
            elif outcome is not None:
                changes.append(f"released {repo} (kept: {outcome.kept()})")
    return changes, failures


def _and_list(names: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _status_row(name: str, *, index: SqliteDependencyIndex, configured: bool) -> SourceStatus:
    key = saved_source_key(name)
    sync_freshness(key, index)
    freshness = FreshnessRecord.fields(
        SOURCE_DATA.freshness(key), max_age=SOURCE_DATA.max_age(), now=now()
    )
    status = index.status(key)
    if not configured:
        state = "missing_source"
    elif freshness["refreshed_at"] is None:
        state = "not_refreshed"
    else:
        state = "stale" if freshness["stale"] else "fresh"
    counts = (
        {}
        if status is None
        else status.model_dump(include={"scanned_at", "repos", "refs", "edges"})
    )
    return SourceStatus(
        source=name, source_key=key, state=state, configured=configured, **counts, **freshness
    )


class SourceGroup(Group):
    """Manage reusable GitHub sources."""

    commands = (Set, Patch, List, Get, Remove, Status, Refresh)
