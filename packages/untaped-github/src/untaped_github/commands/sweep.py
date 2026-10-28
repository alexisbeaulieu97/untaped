"""``untaped github sweep``: exact answers over the stored copies of a scope's repos."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from untaped.sdk import (
    Group,
    Matched,
    Output,
    Parallel,
    Refresh,
    SettingKey,
    Short,
    UsageError,
    key,
)
from untaped_github.application import (
    GitCorpus,
    RepositoryInventoryScope,
    ResolveRepositoryInventory,
    SweepOptions,
    SweepReport,
)
from untaped_github.application import Sweep as SweepUseCase
from untaped_github.commands._github import Scoped, team_scopes
from untaped_github.domain import RefSelector, SweepQuery
from untaped_github.records import SweepFile, SweepMatch, SweepRepo
from untaped_github.settings import SWEEP, GithubSettings

RefProfileOption = Literal["default", "branches", "tags", "all"]


class _Sweep(Scoped):
    """A sweep: its predicates, refs, refresh and exit rules."""

    grep: list[str] = Field(
        default_factory=list,
        description="Content regex, POSIX extended (`a|b`, `\\(`; no `\\d`). Repeatable.",
    )
    not_grep: list[str] = Field(
        default_factory=list, description="Content regex (POSIX extended) that must not match."
    )
    path: list[str] = Field(
        default_factory=list, description="Git pathspec for content predicates."
    )
    has_file: list[str] = Field(default_factory=list, description="File glob that must exist.")
    lacks_file: list[str] = Field(
        default_factory=list, description="File glob that must not exist."
    )
    any_: bool = Field(
        False,
        description=(
            "Match when any positive predicate holds (default: all); --not-grep and "
            "--lacks-file still apply."
        ),
    )
    ignore_case: Annotated[bool, Short("-i")] = Field(
        False, description="Match content case-insensitively."
    )
    fixed_strings: Annotated[bool, Short("-F")] = Field(
        False, description="Treat --grep/--not-grep as literal strings."
    )
    word_regexp: bool = Field(False, description="Match whole words only.")
    refs: RefProfileOption = Field("default", description="Ref profile to sweep.")
    ref: list[str] = Field(default_factory=list, description="Additional ref glob. Repeatable.")
    refresh: Refresh = Refresh(SWEEP)
    strict: bool = Field(False, description="Exit 3 when any repository went unscanned.")
    fail_on_match: bool = Field(False, description="Exit 3 when any repository matches.")
    parallel: Parallel = Parallel(default=key(GithubSettings).sweep.parallel, cap=32)

    def query(self) -> SweepQuery:
        """The query the predicates make; one that asks nothing is a usage error."""
        query = self._query()
        try:
            query.validate()
        except ValueError as exc:
            raise UsageError(str(exc)) from exc
        return query

    def _query(self) -> SweepQuery:
        return SweepQuery(
            greps=tuple(self.grep),
            not_greps=tuple(self.not_grep),
            paths=tuple(self.path),
            has_files=tuple(self.has_file),
            lacks_files=tuple(self.lacks_file),
            any_mode=self.any_,
            ignore_case=self.ignore_case,
            fixed_strings=self.fixed_strings,
            word_regexp=self.word_regexp,
            refs=RefSelector(profile=self.refs, globs=tuple(self.ref)),
        )

    def sweep(self, query: SweepQuery, *, owners: bool) -> SweepReport:
        """Fetch what the query needs and scan it; the footer goes to stderr."""
        stdin_repos, stdin_items = self.piped()
        scope = self._scope(piped=bool(stdin_repos or stdin_items))
        corpus = self.corpus()
        _check_patterns(corpus, query)
        options = SweepOptions(
            scope=scope,
            stdin_repos=stdin_repos,
            archived=self.archived,
            query=query,
            refresh=self.refresh.directive,
            parallel=self.parallel.value,
            owners=owners,
            stdin_items=stdin_items,
        )
        diag = self.ctx.diag
        if self.refresh.directive is False:
            with diag.progress("Sweeping cached repositories") as progress:
                report = SweepUseCase(inventory=lambda _scope: (), corpus=corpus)(
                    options, progress=progress
                )
        else:
            with self.client() as client, diag.progress("Sweeping repositories") as progress:
                inventory = ResolveRepositoryInventory(client)
                report = SweepUseCase(inventory=inventory, corpus=corpus)(
                    options, progress=progress
                )
        self._footer(report)
        return report

    def checked(self, report: SweepReport) -> None:
        """``--strict`` and ``--fail-on-match``: raise :class:`Matched` after the rows."""
        if (self.strict and report.unscanned) or (self.fail_on_match and report.rows):
            raise Matched

    def _scope(self, *, piped: bool) -> RepositoryInventoryScope:
        repos = tuple(self.repo)
        orgs = self.orgs(self.scope, scoped=bool(self.scope.team or repos or piped))
        teams = team_scopes(self.scope.team, orgs=orgs)
        if not orgs and not teams and not repos and not piped:
            raise UsageError(
                "sweep requires --org, --team, --repo, --stdin, or a github.default_org setting"
            )
        return RepositoryInventoryScope(orgs=orgs, teams=teams, repos=repos)

    def _footer(self, report: SweepReport) -> None:
        oldest = report.oldest_fetched_at.isoformat() if report.oldest_fetched_at else "n/a"
        self.ctx.diag.info(
            f"Sweep: {len(report.rows)} matched of {report.scanned} scanned "
            f"({report.refreshed} refreshed, {report.cached} cached), oldest fetch {oldest}"
        )
        for failure in report.unscanned:
            self.ctx.diag.warning(f"unscanned {failure.repo}: {failure.reason}")


class Sweep(_Sweep):
    """One row per matching repo; ``sweep files`` and ``sweep matches`` take the same options.

    An exact answer over the repos of its scopes, kept in the git plugin's
    repo store (``git.store_dir``): regexes, negation, path and file
    predicates, and refs beyond the default branch, which ``search code``
    can't do. It runs ``git``, which must be on ``PATH``. ``sweep files``
    prints one row per matching file instead, ``sweep matches`` the match
    lines.

    Content predicates run ``git grep -I --extended-regexp`` whatever your
    ``grep.patternType``; binary files are skipped, and ``-i``, ``-F`` and
    ``--word-regexp`` apply to every ``--grep`` and ``--not-grep``. A
    content predicate first fetches the file contents it reads, once,
    limited by ``--path``: ``src/*.py`` fetches ``src/``, and ``*.py`` the
    whole tree. File predicates read names only and fetch nothing.

    Each repo's default branch is scanned unless ``--refs`` or ``--ref`` says
    otherwise. Refs are reported by short name; a branch and a tag sharing a
    name are both scanned, as ``heads/NAME`` and ``tags/NAME``. Refs that
    share a tree are grepped once. Each fetch prunes refs deleted upstream
    and fetches only new or moved refs, so an interrupted refresh resumes.

    A repo is fetched when it isn't stored or holds fewer refs than asked
    for, and otherwise once its copy is older than
    ``github.caches.sweep.max_age_seconds`` and GitHub reports a push since
    (or a new default branch); ``--cached`` scans the stored copies only,
    and ``--refresh`` fetches every repo. Transient Git transport failures
    are retried.

    The table has a count column per predicate (``grep:PATTERN``,
    ``has-file:GLOB``), then the matched refs when ``--refs`` or ``--ref``
    asks for more than the default branch, then the CODEOWNERS owners.

    The stderr footer gives matched and scanned counts, refreshed and cached
    counts, the oldest fetch, and each unscanned repo with its reason (a
    name GitHub can't resolve, corrupt metadata, a host that won't send file
    contents by object id, a filesystem or Git error); report those repos as
    unknown, not as non-matching. A failed refresh falls back to a stored
    copy that covers the query, counted as cached, with one ``untaped.stale``
    line for the repo; without one the repo is unscanned. Bad credentials
    (exit 4), rate limits (exit 5), or every explicitly named repo failing
    to resolve stop the whole sweep. Sweeps running at once are safe: the
    store locks each repo while it changes it.

    Examples
    --------
    ```console
    $ untaped github sweep --org acme --grep 'requests\\.get\\(' --path 'src/**'
    $ untaped github sweep --team acme/platform --grep log4j --grep slf4j --any
    $ untaped github sweep matches --org acme --ref 'release/*' --grep jenkins
    ```
    """

    owners: bool = Field(True, description="Report CODEOWNERS for matching repos.")
    output: Output[SweepRepo] = Output(
        columns=(
            key(SweepRepo).full_name,
            key(SweepRepo).hits,
            key(SweepRepo).refs_matched,
            key(SweepRepo).owners,
        )
    )

    @model_validator(mode="after")
    def _columns(self) -> Self:
        """A count column per predicate, named before ``run`` so ``--columns`` is checked first."""
        query = self._query()
        # a matching ref has no hits for a negated predicate, so its column would read 0
        negated = ("not-grep:", "lacks-file:")
        labels = query.labels()
        columns: list[SettingKey | str] = [key(SweepRepo).full_name]
        columns += [label for label in labels if not label.startswith(negated)]
        if query.refs.beyond_default():
            columns.append(key(SweepRepo).refs_matched)
        if self.owners:
            columns.append(key(SweepRepo).owners)
        self.output.columns.show(*columns)
        self.output.columns.offer(*(label for label in labels if label.startswith(negated)))
        return self

    def run(self) -> Iterator[SweepRepo]:
        query = self.query()
        self.output.empty("No matching repositories found.")
        report = self.sweep(query, owners=self.owners)
        yield from report.rows
        self.checked(report)


class Files(_Sweep):
    """Sweep as ``sweep`` does, printing one row per matching file (``hits`` counts its lines)."""

    output: Output[SweepFile]

    def run(self) -> Iterator[SweepFile]:
        self.output.empty("No matching files found.")
        report = self.sweep(self.query(), owners=False)
        yield from _files(report.matches)
        self.checked(report)


class Matches(_Sweep):
    """Sweep as ``sweep`` does, printing each matching line once, with every ref it is on."""

    output: Output[SweepMatch]

    def run(self) -> Iterator[SweepMatch]:
        self.output.empty("No matches found.")
        report = self.sweep(self.query(), owners=False)
        yield from report.matches
        self.checked(report)


class SweepGroup(Group):
    """Sweep repository refs for content and file-presence predicates."""

    runs = Sweep
    commands = (Files, Matches)


def _check_patterns(corpus: GitCorpus, query: SweepQuery) -> None:
    """Refuse a pattern git can't read, before anything is fetched."""
    for flag, pattern in (
        *[("--grep", pattern) for pattern in query.greps],
        *[("--not-grep", pattern) for pattern in query.not_greps],
    ):
        error = corpus.validate_pattern(
            pattern=pattern, paths=query.paths, fixed_strings=query.fixed_strings
        )
        if error is None:
            continue
        path = next((path for path in query.paths if path in error), None)
        if path is not None:
            raise UsageError(f"--path {path!r}: {error}")
        raise UsageError(f"{flag} {pattern!r}: {error}")


def _files(rows: tuple[SweepMatch, ...]) -> list[SweepFile]:
    """Collapse match lines to one row per repo and path; ``hits`` counts its lines."""
    refs: dict[tuple[str, str], dict[str, None]] = {}
    hits: dict[tuple[str, str], int] = {}
    for row in rows:
        place = (row.repo, row.path)
        refs.setdefault(place, {}).update(dict.fromkeys(row.refs))
        hits[place] = hits.get(place, 0) + 1
    return [
        SweepFile(repo=place[0], path=place[1], refs=tuple(refs[place]), hits=hits[place])
        for place in refs
    ]
