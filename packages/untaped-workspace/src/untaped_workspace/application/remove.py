"""Remove a workspace: let go of its repos, drop its rows, and the project workspace made.

``archive`` is reversible: the rows, the holds and the local branches stay,
so ``create`` resumes the work. ``remove`` gives the space back. For every
project of the name, active and archived, it lets go of the repos the
project holds through workspace, through core's holds service (each repo's
plan is :func:`~untaped_workspace.application.release.plan_release`: work in
a worktree or an unpushed branch refuses unless forced, and a repo another
project holds is kept), shown first; one refusal applies nothing. Then
workspace's rows go, and the project itself when workspace made it and it
holds nothing else (``config.yml`` and people's files stay, as with
``untaped project remove``).

The caller holds every project's lock from the plan through the removal.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from untaped.sdk import Failure, OutcomeRecord, UntapedError, plural, shown_path
from untaped_workspace.domain.naming import store_ident
from untaped_workspace.errors import WorkspaceError
from untaped_workspace.records import RemoveOutcome

if TYPE_CHECKING:
    from untaped_workspace.application.ports import WorkspaceStore
    from untaped_workspace.domain.models import WorkspaceRecord

#: How a hold's row reads as a workspace's row.
_ACTIONS = {"forgotten": "released"}


@dataclass(frozen=True)
class RemovalPlan:
    """What ``remove NAME`` would do, from each project's holds as they are now."""

    name: str
    records: tuple[WorkspaceRecord, ...]
    rows: tuple[RemoveOutcome, ...]
    hints: tuple[str, ...] = ()
    previews: tuple[tuple[RemoveOutcome, ...], ...] = ()
    """Each record's repo rows as shown, to apply only what was confirmed."""

    @property
    def blocked(self) -> list[RemoveOutcome]:
        return [row for row in self.rows if row.action in {"refused", "failed"}]


class RemoveWorkspace:
    """``workspace remove``: plan under the projects' locks, then apply (see the module)."""

    def __init__(self, store: WorkspaceStore) -> None:
        self._store = store

    @contextmanager
    def hold(
        self, name: str, located: Sequence[WorkspaceRecord], *, force: bool
    ) -> Iterator[RemovalPlan]:
        """Hold every project's lock; yield the plan for removing them."""
        with ExitStack() as stack:
            records = tuple(stack.enter_context(self._store.locked(r)) for r in located)
            rows: list[RemoveOutcome] = []
            hints: list[str] = []
            previews: list[tuple[RemoveOutcome, ...]] = []
            for index, record in enumerate(records):  # each as its turn will find it
                held = self._store.release(
                    record, force=force, dry_run=True, leaving=records[:index]
                )
                previews.append(tuple(_row(record, outcome) for outcome in held))
                rows += previews[-1]
                hints += [hint for outcome in held if (hint := getattr(outcome, "hint", None))]
                rows.append(_own(record, "planned", _intent(record)))
            yield RemovalPlan(
                name=name,
                records=records,
                rows=tuple(rows),
                hints=tuple(hints),
                previews=tuple(previews),
            )

    def __call__(self, plan: RemovalPlan, *, force: bool) -> list[RemoveOutcome]:
        """Remove ``plan``'s workspaces (the caller holds :meth:`hold`).

        A project whose release is refused or fails keeps workspace's rows,
        so ``remove`` can run again; so does one whose repos changed since
        the preview was shown (new work while the question waited), since
        ``--force`` would discard what nobody confirmed.
        """
        rows: list[RemoveOutcome] = []
        for index, record in enumerate(plan.records):
            earlier = plan.records[:index]
            if index < len(plan.previews) and self._changed(
                record, plan.previews[index], force, earlier
            ):
                why = "kept the workspace: its repos changed since the preview; run remove again"
                changed = WorkspaceError(why, category="conflict")
                rows.append(_own(record, "refused", why, Failure.from_exception(changed)))
                continue
            done = [
                _row(record, outcome)
                for outcome in self._store.release(
                    record, force=force, dry_run=False, leaving=earlier
                )
            ]
            rows += done
            if any(row.action in {"refused", "failed", "planned"} for row in done):
                rows.append(_own(record, "kept", "kept the workspace: a repo is still held"))
                continue
            rows.append(self._drop(record))
        return rows

    def _changed(
        self,
        record: WorkspaceRecord,
        shown: Sequence[RemoveOutcome],
        force: bool,
        earlier: Sequence[WorkspaceRecord],
    ) -> bool:
        """Whether ``record``'s release would now do other than the rows ``shown``."""
        held = self._store.release(record, force=force, dry_run=True, leaving=earlier)
        now = [_row(record, row) for row in held]
        return [_seen(row) for row in now] != [_seen(row) for row in shown]

    def _drop(self, record: WorkspaceRecord) -> RemoveOutcome:
        """The project when workspace made it and it holds nothing else, else workspace's rows."""
        try:
            if record.meta is None or record.meta.created_by != "workspace":
                self._store.forget(record)
                return _own(record, "removed", "dropped the workspace's rows; the project stays")
            removal = self._store.remove_project(record)  # its rows go with plugins/
            if removal.held:
                self._store.forget(record)
                items = plural(removal.held, "item")
                return _own(
                    record,
                    "removed",
                    f"dropped the workspace's rows; the project stays: it holds {items}",
                )
        except UntapedError as exc:
            failure = Failure.from_exception(exc, message=str(exc))
            return _own(record, "failed", str(exc), failure)
        where = shown_path(record.root)
        if removal.kept:
            kept = ", ".join(removal.kept)
            return _own(record, "removed", f"removed the project; kept {where}: {kept}")
        return _own(record, "removed", f"removed the project and {where}")


def _seen(row: RemoveOutcome) -> tuple[str, str, str]:
    """What the preview showed of a repo's row."""
    return (row.repo, row.action, row.detail)


def _intent(record: WorkspaceRecord) -> str:
    if record.meta is not None and record.meta.created_by == "workspace":
        return "drops the workspace's rows, then the project when it holds nothing else"
    return "drops the workspace's rows; the project stays"


def _row(record: WorkspaceRecord, outcome: OutcomeRecord) -> RemoveOutcome:
    """A hold's row (``untaped.hold_outcome``) as the workspace's row for that repo."""
    item = str(getattr(outcome, "item", ""))
    spec = next((s for s in record.repos if store_ident(s.url) == item), None)
    action = str(getattr(outcome, "action", "failed"))
    return RemoveOutcome(
        workspace=record.name,
        repo=spec.name if spec is not None else item,
        action=_ACTIONS.get(action, action),
        detail=str(getattr(outcome, "detail", "")),
        target_path=record.root / spec.dir if spec is not None else record.root,
        error=outcome.error,
    )


def _own(
    record: WorkspaceRecord, action: str, detail: str, error: Failure | None = None
) -> RemoveOutcome:
    """The workspace's own row: an empty ``repo``."""
    return RemoveOutcome(
        workspace=record.name,
        repo="",
        action=action,
        detail=detail,
        target_path=record.root,
        error=error,
    )
