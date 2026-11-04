"""``untaped ansible source``: saved sources, their cached data and its refresh."""

from __future__ import annotations

import shlex
from collections.abc import Iterator

from pydantic import Field, ValidationError

from untaped.sdk import (
    ConfigError,
    Confirm,
    ErrorCategory,
    Failure,
    Group,
    Output,
    Parallel,
    Positional,
    Stdin,
    UntapedError,
    UsageError,
    command_line,
    experimental,
    first_validation_error,
    key,
    most_severe,
    not_found,
    now,
    plural,
    q,
    run_hint,
    size_text,
)
from untaped.sdk.storage import FreshnessRecord
from untaped_ansible.application.refresh_git_index import RefreshResult
from untaped_ansible.commands._ansible import (
    REF_SCAN_DEFAULT,
    AnsibleCommand,
    RefScanDefault,
    saved_source_key,
    unknown_source,
)
from untaped_ansible.commands._changes import Changes, edited
from untaped_ansible.domain.identity import repo_key
from untaped_ansible.errors import AnsibleError
from untaped_ansible.infrastructure import (
    GitSourceStore,
    SqliteDependencyIndex,
    open_index,
)
from untaped_ansible.records import Source, SourceOutcome, SourceStatus
from untaped_ansible.refresh import (
    GIT_PARALLEL_CAP,
    RefreshRecord,
    SelectedSource,
    fill_summary,
    repo_errors,
    serve_sources,
    sync_freshness,
    warn_refresh,
)
from untaped_ansible.settings import SOURCE_DATA, AnsibleSettings
from untaped_ansible.sources import SOURCES, SourceDefinition
from untaped_git.api import Removed, RepoSelector, RepoSource, store_key

NAME = "Source name."


class Set(AnsibleCommand):
    """Create or replace a saved source: the repos a plugin selects, and what to scan in them.

    Pipe the selector a plugin prints (a pipe-format row) with --stdin, or
    name a plugin with --from to take every repo it lists; --match narrows
    either to the repos whose full name matches a glob, beside any patterns
    the piped selector carries. The source's plugin is asked for its repos
    at each refresh, so it must be installed and ready then, not when the
    source is saved.

    It replaces the whole definition, a damaged row of that name included;
    `source patch` adds or removes one
    path or ref selector. A change drops the source's cached data, so the
    next `deps`, `impact`, `find` or `graph` that reads it refreshes it
    first.

    Examples
    --------
    ```console
    $ untaped ansible source set infra --from gitlab --match 'infra/*'
    ```
    """

    stability = experimental  # it reads and stores the git plugin's experimental RepoSelector

    name: Positional[str] = Field(description=NAME)
    stdin: Stdin[RepoSelector] = Stdin(via=RepoSource.to_selector)
    from_: str | None = Field(
        None, description="Plugin whose repos the source takes: every repo it lists."
    )
    match: list[str] = Field(
        default_factory=list,
        description="Glob a repo's full name must match (infra/*), any case; repeat for any.",
    )
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
        plugin, selector = self._selection()
        try:
            source = SourceDefinition(
                name=self.name,
                source=plugin,
                selector=selector.model_copy(
                    update={"match": list(dict.fromkeys([*selector.match, *self.match]))}
                ),
                dependency_paths=self.path,
                ref_kinds=self.ref_kind,
                ref_patterns=self.ref_pattern,
                ref_scan_default=self.ref_scan_default,
            )
        except ValidationError as exc:
            raise UsageError(first_validation_error(exc)) from exc
        action = self._action(source)
        if action != "unchanged":
            SOURCES.put(source)
            open_index().clear(saved_source_key(self.name))
        return SourceOutcome(action=action, name=self.name)

    def _action(self, source: SourceDefinition) -> str:
        """``created``, ``updated`` or ``unchanged``; a damaged row of the name is dropped."""
        try:
            previous = SOURCES.get(self.name)
        except ConfigError:
            names = SOURCES.keys()  # never stops on a damaged row
            if self.name not in names:
                raise
            # Such as a row an older version saved: set replaces a definition whole.
            SOURCES.delete(self.name)
            return "updated"
        return "created" if previous is None else "unchanged" if previous == source else "updated"

    def _selection(self) -> tuple[str, RepoSelector]:
        """The plugin the source asks, and its selector: the one piped, or none of its own."""
        if not self.stdin.requested:
            if self.from_ is None:
                raise UsageError(
                    "source set needs --stdin (a selector a plugin issued) or --from PLUGIN"
                )
            return self.from_, RepoSelector()
        if self.from_ is not None:
            raise UsageError("--from names the plugin of a source without a piped selector")
        rows = self.stdin.rows()
        if len(rows) != 1:
            raise UsageError(f"source set reads one selector from stdin, got {len(rows)}")
        issued = rows[0].source
        if issued is None:
            raise UsageError(
                "the piped selector names no plugin that issued it",
                hint="pipe a selector a plugin issues, or use --from PLUGIN --match PATTERN",
            )
        return issued.plugin, rows[0]


class Patch(AnsibleCommand):
    """Patch what a saved source scans: its dependency paths and ref selectors.

    Its selector is replaced whole with `source set`. A change drops the
    source's cached data, as `source set` does; `changes` on the outcome
    lists what was added or removed.

    Examples
    --------
    ```console
    $ untaped ansible source patch platform --add-path meta/main.yml --add-ref-kind tags
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
        SOURCES.put(source)
        open_index().clear(saved_source_key(self.name))
        return SourceOutcome(action="updated", name=self.name, changes=changes)


class List(AnsibleCommand):
    """List saved sources."""

    stability = experimental  # its rows carry the git plugin's experimental RepoSelector

    output: Output[Source]

    def run(self) -> Iterator[Source]:
        self.output.empty(
            f"No sources configured. Add one with `{command_line(Set.spelled('NAME'))}`."
        )
        for source in SOURCES.all():
            yield _source_row(source)


class Get(AnsibleCommand):
    """Show one saved source."""

    stability = experimental  # its row carries the git plugin's experimental RepoSelector

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
        # Keys, not rows: a damaged row is removed here, and doesn't stop removing another.
        names = SOURCES.keys()
        if self.name not in names:
            raise AnsibleError(unknown_source(self.name), category="not_found")
        dry_run = not self.confirm(f"Remove source {q(self.name)} and its cached source data?")
        index = open_index()
        key = saved_source_key(self.name)
        others = {saved_source_key(name): name for name in names if name != self.name}
        selected = index.selecting_sources(others)
        changes, failures = _release_repos(
            index.source_repos(key),
            {repo: sorted(others[source] for source in keys) for repo, keys in selected.items()},
            store=GitSourceStore(plugin=self.ctx.plugin),
            dry_run=dry_run,
        )
        if not dry_run and not failures:
            SOURCES.delete(self.name)
            index.clear(key)
            SOURCE_DATA.forget(key)
        for repo, failure in failures:
            self.ctx.diag.failure(failure, item=repo)
        if failures:
            self.ctx.diag.hint(run_hint(Remove.spelled(shlex.quote(self.name))))
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
        configured = {source.name: source for source in SOURCES.all()}
        if self.name is not None and self.name not in configured:
            raise AnsibleError(
                not_found("source", self.name, known=sorted(configured)), category="not_found"
            )
        self.output.empty(f"No sources scanned yet. Run `{command_line(Refresh.spelled('NAME'))}`.")
        for name in [self.name] if self.name is not None else sorted(configured):
            yield _status_row(name, index=index, configured=name in configured)


class Refresh(AnsibleCommand):
    """Refresh a saved source: ask its plugin for its repos, then scan them.

    The source's plugin is asked live for the repos its selector takes; a
    plugin that can't read its selector back lists every repo instead,
    narrowed by the source's match patterns, with a warning saying so.

    --parallel defaults to ansible.git_fetch_parallel and is capped at 32.
    Progress goes to stderr. A failure for one repo does not stop the
    refresh: successes are saved, each failure prints `error: REPO: reason`,
    and the run exits 1, or 5 when any failure was transient, with
    `refresh completed with N repo failures; successes were saved`. Rerun
    it to retry only the failures. When every repo fails, the previous data
    is kept (`index left unchanged`).

    Each repo's branches and tags are probed by the plugin that serves its
    host, many repos per request when that plugin can, else with
    `git ls-remote`, `git.probe_parallel` at once. A repo the host's probe
    could not read is read with `git ls-remote` instead, with one warning
    for all of them; a repo the host says does not exist fails.

    Repos are probed and saved in batches of
    `ansible.source_refresh_repo_batch_size`. Once a host's probe budget is
    spent, the refresh stops after the batch with the finished batches saved
    and exits 5, and the error's `retry_at` says when the budget resets;
    rerunning resumes where it left off. A probe the host refused, such as
    a rejected token, aborts the whole refresh instead.

    Examples
    --------
    ```console
    $ untaped ansible source refresh platform --parallel 16
    ```
    """

    name: Positional[str] = Field(description=NAME)
    parallel: Parallel = Parallel(
        default=key(AnsibleSettings).git_fetch_parallel, cap=GIT_PARALLEL_CAP
    )

    def run(self) -> None:
        name = self.name
        source = self.saved(name)
        key = saved_source_key(name)
        wiring = self.wiring(open_index(), parallel=self.parallel.value)
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
                warn_refresh(result, self.ctx.diag.warning)
            if error is not None:
                raise error from None  # the fill's own, without serve()'s hint to --cached
            if exc.details.get("key") == key:
                # serve() raised what the run this one waited on left (partial, failed)
                self.ctx.diag.info(self._refreshed_meanwhile())
                exc.hint = f"rerun `{command_line(Refresh.spelled(shlex.quote(name)))}` to retry"
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
        warn_refresh(result, self.ctx.diag.warning)

    def _report_repo_failures(self, result: RefreshResult) -> None:
        """One failure line per repo the refresh could not read, and a rerun hint when it helps."""
        for failure, error in zip(result.failures, repo_errors(result), strict=True):
            self.ctx.diag.failure(error, item=failure.repo)
        if any(failure.category == ErrorCategory.UNAVAILABLE for failure in result.failures):
            self.ctx.diag.hint(
                f"rerun `{command_line(Refresh.spelled(shlex.quote(self.name)))}`; "
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
    return Source.model_validate({**source.model_dump(), "selects": _selects(source.selector)})


def _selects(selector: RepoSelector) -> str:
    """What ``selector`` takes, in words: its plugin's record field by field, then ``match``.

    The record is the plugin's own: each field is shown as given, never read.
    """
    record = {} if selector.source is None else selector.source.record
    parts = [
        f"{field} {', '.join(map(str, value)) if isinstance(value, list) else value}"
        for field, value in {**record, "match": selector.match}.items()
        if value not in (None, "", [], {})
    ]
    return "; ".join(parts) or "every repo"


def _release_repos(
    repos: dict[str, tuple[str, ...]],
    selected: dict[str, list[str]],
    *,
    store: GitSourceStore,
    dry_run: bool,
) -> tuple[list[str], list[tuple[str, UntapedError]]]:
    """Release ``repos`` (name → URLs) no other saved source selects.

    Each repo is tried at its stored URL and every URL its scans fetched,
    once per store repo (an https and an ssh URL of one repo are one). Repos ansible has no
    part in are skipped. Returns the outcome lines and the failed
    releases.
    """
    changes: list[str] = []
    failures: list[tuple[str, UntapedError]] = []
    for repo, urls in repos.items():
        keepers = selected.get(repo_key(repo))
        if keepers:
            noun = "source" if len(keepers) == 1 else "sources"
            changes.append(f"kept {repo} (selected by {noun} {_and_list(keepers)})")
            continue
        for url in {store_key(url): url for url in urls}.values():
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
    """Manage saved sources: the repos a plugin selects, scanned for dependencies."""

    commands = (Set, Patch, List, Get, Remove, Status, Refresh)
