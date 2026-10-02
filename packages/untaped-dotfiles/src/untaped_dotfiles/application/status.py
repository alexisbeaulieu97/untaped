"""``dotfiles status``: the state of every placement, offline, plus the repo rows and summary."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from untaped.sdk import ErrorInfo, UntapedError, note_failure
from untaped_dotfiles.domain.hashing import content_hash, value_hash
from untaped_dotfiles.domain.models import AppliedRecord, RepoRecord
from untaped_dotfiles.domain.records import RepoRow, StatusRow, StatusSummary
from untaped_dotfiles.domain.status import FileState, SourceInfo, file_state, needs_attention
from untaped_dotfiles.errors import DotfilesError

if TYPE_CHECKING:
    from untaped_dotfiles.application.inventory import Inventory, Placement
    from untaped_dotfiles.application.ports import DotfilesStore, GitRepos, Placer, SourceTree


@dataclass(frozen=True)
class Evaluation:
    """One placement's state and what an apply would record about its source."""

    state: FileState
    detail: str
    record: AppliedRecord | None
    source_hash: str | None
    commit: str | None
    error: ErrorInfo | None = None


class Evaluator:
    """Computes :class:`Evaluation` for placements, caching per-repo git reads."""

    def __init__(
        self, store: DotfilesStore, git: GitRepos, placer: Placer, inventory: Inventory
    ) -> None:
        self._store = store
        self._git = git
        self._placer = placer
        self._inventory = inventory
        self._changed: dict[str, set[str]] = {}
        self._trees: dict[str, SourceTree] = {}
        self._records: dict[str, AppliedRecord] | None = None

    def reset(self) -> None:
        """Forget cached git reads and records (after a pull or an apply)."""
        self._changed.clear()
        self._trees.clear()
        self._records = None

    def record(self, placement: Placement) -> AppliedRecord | None:
        if self._records is None:
            self._records = {r.target: r for r in self._store.applied()}
        return self._records.get(placement.key)

    def tree(self, repo: RepoRecord) -> SourceTree:
        if repo.name not in self._trees:
            self._trees[repo.name] = self._inventory.source_tree(repo)
        return self._trees[repo.name]

    def prime_changes(self, placements: Sequence[Placement]) -> None:
        """Read, once per managed repo, which link sources the fetched ref changed."""
        by_repo: dict[str, set[str]] = {}
        repos: dict[str, RepoRecord] = {}
        for p in placements:
            if p.mode == "link" and p.repo.managed and not p.excluded:
                by_repo.setdefault(p.repo.name, set()).add(p.source)
                repos[p.repo.name] = p.repo
        for name, sources in by_repo.items():
            if name in self._changed:
                continue
            repo = repos[name]
            try:
                self._changed[name] = self._git.changed_paths(
                    Path(repo.path), repo.ref, sorted(sources)
                )
            except UntapedError:
                self._changed[name] = set()

    def source(self, placement: Placement) -> SourceInfo:
        repo = placement.repo
        if placement.orphan:  # the manifest no longer places it, whatever the tree holds
            return SourceInfo(exists=False)
        if placement.mode == "link":
            destination = self._inventory.link_destination(repo, placement.source)
            in_tree = destination.exists() or destination.is_symlink()
            changed = placement.source in self._changed.get(repo.name, set())
            if not in_tree and repo.managed:
                in_tree = self.tree(repo).exists(placement.source)
                changed = changed or in_tree
            return SourceInfo(exists=in_tree, hash=str(destination), changed=changed)
        tree = self.tree(repo)
        if not tree.exists(placement.source) or tree.is_dir(placement.source):
            return SourceInfo(exists=False)
        data = tree.read(placement.source)
        return SourceInfo(exists=True, hash=self.source_hash(placement, data))

    def source_hash(self, placement: Placement, data: bytes) -> str:
        if placement.mode == "merge" and placement.fmt is not None:
            try:
                return value_hash(self._placer.parse(data, fmt=placement.fmt))
            except DotfilesError:
                return content_hash(data)
        return content_hash(data)

    def evaluate(self, placement: Placement) -> Evaluation:
        if placement.excluded is not None:
            return Evaluation("excluded", placement.excluded, None, None, None)
        record = self.record(placement)
        try:
            source = self.source(placement)
            target = self._placer.observe(
                placement.target,
                fmt=placement.fmt,
                managed=record.managed if record is not None else (),
            )
        except UntapedError as exc:
            return Evaluation(
                "orphan", str(exc), record, None, None, error=note_failure(exc, message=str(exc))
            )
        state, detail = file_state(placement.mode, record, source, target)
        error = None
        if state == "orphan" and record is None:
            failure = DotfilesError(f"{placement.source}: {detail}", category="not_found")
            error = note_failure(failure, message=detail)
        commit = None if placement.mode == "link" else self.tree(placement.repo).commit
        if placement.mode == "link" and placement.repo.managed:
            commit = self._head(placement.repo)
        return Evaluation(state, detail, record, source.hash, commit, error=error)

    def _head(self, repo: RepoRecord) -> str | None:
        try:
            return self._git.head(Path(repo.path))
        except UntapedError:
            return None


class StatusReader:
    """One :class:`StatusRow` per placement, the repo rows, and the summary."""

    def __init__(
        self,
        store: DotfilesStore,
        git: GitRepos,
        evaluator: Evaluator,
        *,
        now: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._git = git
        self._evaluator = evaluator
        self._now = now

    def rows(self, placements: Sequence[Placement]) -> list[StatusRow]:
        self._evaluator.prime_changes(placements)
        rows: list[StatusRow] = []
        for placement in placements:
            found = self._evaluator.evaluate(placement)
            rows.append(
                StatusRow(
                    repo=placement.repo.name,
                    item=placement.item,
                    file=placement.file,
                    source=placement.source,
                    mode=placement.mode,
                    policy=placement.choice.policy,
                    state=found.state,
                    detail=found.detail,
                    target_path=placement.target,
                    error=found.error,
                )
            )
        return rows

    def repo_rows(self) -> list[RepoRow]:
        counts = Counter(choice.repo for choice in self._store.items())
        rows: list[RepoRow] = []
        for repo in self._store.repos():
            path = Path(repo.path)
            head: str | None = None
            behind: int | None = None
            try:
                head = self._git.head(path)
                behind = self._git.behind(path, repo.ref)
            except UntapedError:
                pass
            rows.append(
                RepoRow(
                    name=repo.name,
                    url=repo.url,
                    ref=repo.ref,
                    managed=repo.managed,
                    head=head,
                    behind=behind,
                    items=counts.get(repo.name, 0),
                    target_path=path,
                )
            )
        return rows

    def summary(self, rows: Sequence[StatusRow], *, repos_behind: int = 0) -> StatusSummary:
        counted = Counter(row.state for row in rows if row.state != "excluded")
        attention = sum(
            1 for row in rows if needs_attention(row.policy, row.state) or row.error is not None
        )
        return StatusSummary(
            checked_at=self._now(),
            total=sum(counted.values()),
            attention=attention,
            pending=counted["pending"],
            foreign=counted["foreign"],
            applied=counted["applied"],
            behind=counted["behind"],
            modified=counted["modified"],
            conflict=counted["conflict"],
            missing=counted["missing"],
            orphan=counted["orphan"],
            repos_behind=repos_behind,
        )
